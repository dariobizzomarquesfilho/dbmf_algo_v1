"""P/B vs ROE ATR Trailing Stop Strategy for QuantConnect Lean.

Screens S&P 500 constituents using embedded yfinance data for P/B, ROE, Beta.
All data is embedded in Python modules (data/*.py) so it works inside the
Lean Docker container with NO external .csv.zip or .json files needed.
Prices are injected into securities via Security.SetMarketPrice().

Filters out financials by sector keyword matching.
Exits positions when ATR trailing stop is breached.
All positions equal-weight (1/max_positions).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from AlgorithmImports import *
from data.sp500_data import load_sp500_membership
from data.corporate_actions import corporate_action_exits
from universe.pb_roe_universe import run_fine_selection
from indicators.atr_trailing_stop import compute_atr_trailing_stop
from data.equity_bars import load_equity_bars


class PbRoeAtrAlgorithm(QCAlgorithm):
    """P/B vs ROE screening with ATR trailing stop exit."""

    def Initialize(self):
        self.SetCash(100_000)
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)
        self.UniverseSettings.Resolution = Resolution.Daily

        self.max_positions = 10
        self.atr_period = 15
        self.atr_multiplier = 3.0
        self.cooldown_days = 30

        # Load backtest window from single source of truth (config/.env → embedded)
        try:
            from data.backtest_config import load_backtest_window
        except ImportError as _e:
            raise RuntimeError(
                "data module 'backtest_config' is missing — run "
                "lean_project/scripts/embed_data.py before backtesting"
            ) from _e
        window = load_backtest_window()
        start_y, start_m, start_d = map(int, window["start"].split("-"))
        end_y, end_m, end_d = map(int, window["end"].split("-"))
        self.SetStartDate(start_y, start_m, start_d)
        self.SetEndDate(end_y, end_m, end_d)

        # Load all data from embedded modules (no disk I/O, Docker-safe)
        self.bars_cache = load_equity_bars()
        try:
            from data.fundamentals_history import load_fundamentals_history
            self.fundamentals_history = load_fundamentals_history()
        except ImportError:
            self.fundamentals_history = {}
        try:
            from data.damodaran_erp_history import load_damodaran_erp_history
            self.erp_history_cache = load_damodaran_erp_history()
        except ImportError:
            self.erp_history_cache = {}
        self.market_bars = self.bars_cache.get("^GSPC", {})

        # Guard: fail loudly if embedded equity bars don't cover the
        # configured backtest window (a stale regeneration would otherwise
        # silently constrain the backtest to the old data range).
        all_dates = set()
        for _ticker_bars in self.bars_cache.values():
            all_dates.update(_ticker_bars.keys())
        if all_dates:
            _earliest, _latest = min(all_dates), max(all_dates)
            if _earliest > window["start"] or _latest < window["end"]:
                self.Error(
                    "EQUITY BAR COVERAGE MISMATCH: embedded bars span "
                    f"{_earliest}..{_latest} but backtest window is "
                    f"{window['start']}..{window['end']}. Re-run "
                    "download_equity_data.py then embed_data.py."
                )

        # Load S&P 500 PIT membership
        import csv
        from pathlib import Path
        sp500_csv = Path(__file__).resolve().parent / "data" / "sp500_ticker_start_end.csv"
        self.sp500_membership = {}
        if sp500_csv.exists():
            with open(sp500_csv, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ticker = row["ticker"].strip()
                    if not ticker:
                        continue
                    start = row["start_date"].strip()
                    end = row["end_date"].strip() or None
                    self.sp500_membership.setdefault(ticker, []).append((start, end))

        # Bootstrap equity CSV.zip files from embedded bars so Lean's data feed
        # has bars (time loop only advances on data events).
        import os
        from data.bootstrap_data import bootstrap, _find_data_dirs
        cwd = os.getcwd()
        n_written = bootstrap(cwd)
        self.Log(f"DIAG cwd={cwd} data_dirs={[str(d) for d in _find_data_dirs(cwd)]} bootstrap_wrote={n_written}")

        # Register all S&P 500 tickers as equities (for SetHoldings/order execution)
        self._registered = []
        date_str = self.Time.strftime("%Y-%m-%d")
        for ticker, entries in self.sp500_membership.items():
            if ticker == "^TNX":
                continue
            if not any(start <= date_str and (end is None or date_str <= end) for start, end in entries):
                continue
            # Safety net: only register tickers we actually have bars for. Names
            # the download dropped and recovery couldn't source (foreign-listing
            # collisions, 404s) land in equity_unavailable.json and would
            # otherwise produce failed data requests.
            if ticker not in self.bars_cache:
                continue
            try:
                self.AddEquity(ticker, Resolution.Daily)
                self._registered.append(ticker)
            except Exception as e:
                self.Log(f"WARN: failed to register {ticker}: {e}")

        self.trailing_stops = {}
        self.selected_symbols = set()
        self.sell_dates = {}  # symbol_str -> date_str when last liquidated (cooldown)
        self.last_rebalance_date = None
        self._cached_selection = None
        self._symbols = {
            ticker: Symbol.Create(ticker, SecurityType.Equity, Market.USA)
            for ticker in self._registered
        }

        self.Log(f"PbRoeAtrAlgorithm initialized — {len(self._registered)} tickers, embedded data")

    # ------------------------------------------------------------------
    # Price injection from embedded bars
    # ------------------------------------------------------------------
    def _ensure_prices(self, symbol_strs=None):
        """Set Security.Price from embedded bars for today's date.

        The daily bar feed populates HasData but Security.Price can lag/read 0
        during scheduled events. Set it explicitly from embedded bars so
        SetHoldings/Liquidate/ATR checks all use correct prices.
        """
        date_str = self.Time.strftime("%Y-%m-%d")
        if symbol_strs is None:
            symbol_strs = self._registered
        for symbol_str in symbol_strs:
            symbol = self._symbols.get(symbol_str)
            if symbol is None or symbol not in self.Securities:
                continue
            bar = self.bars_cache.get(symbol_str, {}).get(date_str)
            if bar is None:
                continue
            trade = TradeBar()
            trade.Symbol = symbol
            trade.Time = self.Time
            trade.Open = float(bar["open"])
            trade.High = float(bar["high"])
            trade.Low = float(bar["low"])
            trade.Close = float(bar["close"])
            trade.Volume = float(bar.get("volume", 0))
            self.Securities[symbol].SetMarketPrice(trade)

    # ------------------------------------------------------------------
    # Universe selection
    # ------------------------------------------------------------------
    def _is_sp500_member(self, ticker: str, date_str: str) -> bool:
        entries = self.sp500_membership.get(ticker, [])
        for start, end in entries:
            if start <= date_str and (end is None or date_str <= end):
                return True
        return False

    def CoarseSelection(self, coarse):
        """Filter to S&P 500 constituents active as of today."""
        date_str = self.Time.strftime("%Y-%m-%d")
        return [
            c.Symbol for c in coarse
            if self._is_sp500_member(c.Symbol.Value, date_str)
        ]

    def FineSelection(self, fine):
        """Screen with embedded fundamentals (no QC paid feed, no disk I/O).

        Fully point-in-time: fundamentals, beta, rf, and ERP are all
        looked up as-of the current backtest date (no look-ahead).
        """
        tickers = [f.Symbol.Value for f in fine]
        self._cached_selection = run_fine_selection(
            algorithm=self,
            tickers=tickers,
            max_positions=self.max_positions,
            bars_cache=self.bars_cache,
            history_cache=self.fundamentals_history,
            market_bars=self.market_bars,
            erp_history_cache=self.erp_history_cache,
        )
        return self._cached_selection

    # ------------------------------------------------------------------
    # Rebalance trigger (OnData-based, replaces AfterMarketClose schedule
    # which only fires once with daily data in this Lean setup)
    # ------------------------------------------------------------------
    def OnData(self, data):
        """Trigger daily rebalance on each new trading day."""
        today = self.Time.date()
        if self.last_rebalance_date == today:
            return
        self.last_rebalance_date = today
        self.DailyRebalance()

    # ------------------------------------------------------------------
    # Corporate-action exits (renames/mergers/delistings + spinoffs)
    # ------------------------------------------------------------------
    def _corporate_action_exits(self, date_str: str) -> set:
        """Tickers to liquidate today due to a membership-ending event or a spinoff.

        Delegates to the shared ``corporate_action_exits`` helper (membership
        ends + spinoff parents).
        """
        return corporate_action_exits(
            self.sp500_membership, self.selected_symbols, date_str
        )

    # ------------------------------------------------------------------
    # Rebalance
    # ------------------------------------------------------------------
    def DailyRebalance(self):
        """Daily screening and rebalance using embedded data."""
        date_str = self.Time.strftime("%Y-%m-%d")
        self.Log(f"DailyRebalance {date_str} — positions={len(self.selected_symbols)}")

        # Corporate-action exits run at the very start of the rebalance, before
        # ATR stops and before any screen, so we never hold a renamed/delisted
        # or spinoff-parent position into the action day.
        exits = self._corporate_action_exits(date_str)
        for sym in list(self.selected_symbols):
            if sym in exits:
                self._liquidate_symbol(sym)

        # Ensure every security has a price (feed may lag Security.Price)
        self._ensure_prices()

        # Check ATR stops on existing positions (cheap: prices + ATR only,
        # no fundamentals/beta regression)
        self._check_stops()

        # Expensive PIT screen (fundamental lookups + 252d beta regression)
        # runs ONLY when a buy opportunity exists: a slot is free (initial
        # fill or a stop just freed one). Fully-held days skip it entirely.
        if len(self.selected_symbols) < self.max_positions:
            selected = getattr(self, "_cached_selection", None)
            if selected is None:
                selected = run_fine_selection(
                    algorithm=self,
                    tickers=list(self.fundamentals_history.keys()),
                    max_positions=self.max_positions,
                    bars_cache=self.bars_cache,
                    history_cache=self.fundamentals_history,
                    market_bars=self.market_bars,
                    erp_history_cache=self.erp_history_cache,
                )

            # Never re-add a ticker that has a corporate-action exit today
            # (rename/merger/delisting or spinoff parent) within the same cycle.
            selected = [s for s in selected if s not in exits]

            selected_set = set(selected)

            # Liquidate removed positions
            for symbol_str in list(self.selected_symbols):
                if symbol_str not in selected_set:
                    self._liquidate_symbol(symbol_str)

            # Add new positions
            from datetime import datetime, timedelta
            today = self.Time.date()
            for symbol_str in selected:
                if symbol_str in self.selected_symbols:
                    continue
                # Cooldown: don't re-buy a symbol recently sold (e.g. stop exit)
                sold_on = self.sell_dates.get(symbol_str)
                if sold_on is not None:
                    sold_date = datetime.strptime(sold_on, "%Y-%m-%d").date()
                    if (today - sold_date) < timedelta(days=self.cooldown_days):
                        continue
                symbol = self._symbols.get(symbol_str)
                if symbol is None or symbol not in self.Securities or not self.Securities[symbol].HasData:
                    self.Log(f"SKIP {symbol_str}: no price data yet")
                    continue
                weight = 1.0 / max(len(selected), 1)
                self.SetHoldings(symbol, weight)
                self.trailing_stops[symbol_str] = self._compute_stop(symbol_str)
                self.Log(f"Added {symbol_str} @ {weight:.1%}")

            self.selected_symbols = selected_set

    # ------------------------------------------------------------------
    # ATR stops
    # ------------------------------------------------------------------
    def _check_stops(self):
        """Check ATR trailing stops on all current positions."""
        date_str = self.Time.strftime("%Y-%m-%d")
        for symbol_str in list(self.selected_symbols):
            symbol = self._symbols.get(symbol_str)
            if symbol is None or symbol not in self.Securities:
                continue
            if not self.Portfolio[symbol].Invested:
                continue
            # Use embedded bar close (Security.Price may be stale/0)
            bar = self.bars_cache.get(symbol_str, {}).get(date_str)
            if bar is None:
                self.Log(f"STOP CHECK {symbol_str} {date_str}: no bar data")
                continue
            price = float(bar["close"])
            if price <= 0:
                continue
            stop = self._compute_stop(symbol_str, self.trailing_stops.get(symbol_str))
            if stop is None:
                self.Log(f"STOP CHECK {symbol_str} {date_str}: stop=None (insufficient data)")
                continue
            self.trailing_stops[symbol_str] = stop
            if price <= stop:
                self.Log(f"ATR STOP: {symbol_str} price={price:.2f} stop={stop:.2f} LIQUIDATE")
                self.Liquidate(symbol)
                self.selected_symbols.discard(symbol_str)
                self.sell_dates[symbol_str] = date_str  # start cooldown
            else:
                self.Log(f"STOP CHECK {symbol_str} {date_str}: price={price:.2f} stop={stop:.2f} OK")

    def _liquidate_symbol(self, symbol_str: str):
        """Liquidate and remove from tracking."""
        symbol = self._symbols.get(symbol_str)
        if symbol is not None and symbol in self.Securities and self.Portfolio[symbol].Invested:
            self.Log(f"Removed {symbol_str}: LIQUIDATE")
            self.Liquidate(symbol)
        self.sell_dates[symbol_str] = self.Time.strftime("%Y-%m-%d")  # start cooldown
        self.trailing_stops.pop(symbol_str, None)

    def _compute_stop(self, symbol_str: str, prev_stop: Optional[float] = None) -> Optional[float]:
        # as_of_date = today, so the stop uses only bars up to the current day
        # (prevents look-ahead bias from full-cache sorting).
        return compute_atr_trailing_stop(
            symbol_str,
            self.bars_cache,
            self.atr_period,
            self.atr_multiplier,
            "SMA",
            as_of_date=self.Time.strftime("%Y-%m-%d"),
            prev_stop=prev_stop,
        )

    def OnEndOfAlgorithm(self):
        self.Log("=" * 60)
        self.Log("BACKTEST COMPLETE")
        self.Log(f"Period: {self.StartDate} to {self.EndDate}")
        self.Log(f"Final Value: {self.Portfolio.TotalPortfolioValue:,.2f}")
        invested = sum(
            1 for s in self.Portfolio.Keys if self.Portfolio[s].Invested
        )
        self.Log(f"Positions Held: {invested}")
        self.Log("=" * 60)
