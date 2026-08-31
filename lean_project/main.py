"""P/B vs ROE ATR Trailing Stop Strategy for QuantConnect Lean.

Screens S&P 500 constituents using embedded yfinance data for P/B, ROE, Beta.
All data is embedded in Python modules (data/*.py) so it works inside the
Lean Docker container with NO external .csv.zip or .json files needed.
Prices are injected into securities via Security.SetMarketPrice().

Filters out financials by sector keyword matching.
Exits positions when ATR trailing stop is breached.
Position sizing uses Van Tharp ATR-risk: each position risks 1% of NAV
(entry - 3*ATR stop), capped at 10% of NAV at entry (whole shares).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

from AlgorithmImports import *
from data.sp500_data import intervals_active
from data.corporate_actions import corporate_action_exits
from universe.pb_roe_universe import run_fine_selection, log_missing_g_eps_summary
from indicators.atr_trailing_stop import compute_atr_trailing_stop
from data.equity_bars import load_equity_bars


def _van_tharp_shares(
    nav: float,
    entry_price: float,
    entry_stop: float,
    risk_pct: float = 0.01,
    max_pct: float = 0.10,
) -> tuple[int, float, float, float]:
    """Pure Van Tharp sizing — whole shares only, no fractional.

    Returns (shares, capped_dollars, van_tharp_dollars, risk_per_share).
    Caller must validate nav/entry_price/entry_stop >0 and finite before calling.
    """
    risk_per_share = entry_price - entry_stop
    risk_dollars = nav * risk_pct
    van_tharp_dollars = risk_dollars * entry_price / risk_per_share if risk_per_share > 0 else 0.0
    capped_dollars = min(van_tharp_dollars, nav * max_pct)
    shares = int(capped_dollars / entry_price) if entry_price > 0 else 0
    return shares, capped_dollars, van_tharp_dollars, risk_per_share


class PbRoeAtrAlgorithm(QCAlgorithm):
    """P/B vs ROE screening with ATR trailing stop exit."""

    def Initialize(self):
        self.SetCash(100_000)
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Cash)
        self.UniverseSettings.Resolution = Resolution.Daily

        self.atr_period = 15
        self.atr_multiplier = 3.0
        self.cooldown_days = 30
        # Van Tharp ATR-risk sizing: 1% of NAV risk per position, capped at 10% NAV
        # No max_positions cap — portfolio size is risk/cash-constrained (whole shares)
        self.risk_per_trade_pct = 0.01
        self.max_position_pct = 0.10
        self.fine_selection_limit = 20
        self.entry_prices: dict[str, float] = {}

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
        try:
            self.bars_cache = load_equity_bars()
        except (ImportError, FileNotFoundError) as _e:
            raise RuntimeError(
                "data module 'equity_bars' is missing — run "
                "lean_project/scripts/embed_data.py before backtesting"
            ) from _e
        try:
            from data.fundamentals_history import load_fundamentals_history
            self.fundamentals_history = load_fundamentals_history()
        except ImportError as _e:
            raise RuntimeError(
                "data module 'fundamentals_history' is missing — run "
                "lean_project/scripts/embed_data.py before backtesting"
            ) from _e
        try:
            from data.damodaran_erp_history import load_damodaran_erp_history
            self.erp_history_cache = load_damodaran_erp_history()
        except ImportError as _e:
            raise RuntimeError(
                "data module 'damodaran_erp_history' is missing — run "
                "lean_project/scripts/embed_data.py before backtesting"
            ) from _e
        self.market_bars = self.bars_cache.get("^GSPC", {})
        if not self.market_bars:
            msg = (
                "MARKET BARS MISSING: ^GSPC not in embedded equity_bars — "
                "rolling_beta will always return None and the screen will be empty. "
                "Re-run download_equity_data.py then embed_data.py."
            )
            self.Error(msg)
            raise RuntimeError(msg)

        # Guard: fail loudly if embedded equity bars don't cover the
        # configured backtest window (a stale regeneration would otherwise
        # silently constrain the backtest to the old data range).
        all_dates = set()
        for _ticker_bars in self.bars_cache.values():
            all_dates.update(_ticker_bars.keys())
        if all_dates:
            _earliest, _latest = min(all_dates), max(all_dates)
            # Tolerate a short gap at the tail (weekend/holiday) so a 1-day
            # offset between the last bar and BACKTEST_END doesn't raise a
            # spurious mismatch (the embed step already allows 4 days).
            _tail_gap = (
                datetime.strptime(window["end"], "%Y-%m-%d").date()
                - datetime.strptime(_latest, "%Y-%m-%d").date()
            ).days
            if _earliest > window["start"] or _tail_gap > 4:
                msg = (
                    "EQUITY BAR COVERAGE MISMATCH: embedded bars span "
                    f"{_earliest}..{_latest} but backtest window is "
                    f"{window['start']}..{window['end']}. Re-run "
                    "download_equity_data.py then embed_data.py."
                )
                self.Error(msg)
                raise RuntimeError(msg)

        # Guard: fail loudly if the embedded fundamentals universe is too thin.
        # A stale/shrunk embed (e.g. only AAPL/MSFT) would silently cap the
        # screen to those names and produce a ~2-ticker backtest with no error.
        _fh_count = len(self.fundamentals_history)
        if _fh_count < 100:
            msg = (
                "FUNDAMENTALS UNIVERSE TOO SMALL: embedded fundamentals_history "
                f"covers only {_fh_count} symbols (expected >= 100). A stale build "
                "produced a near-empty universe. Re-run download_edgartools_data.py "
                "then embed_data.py before backtesting."
            )
            self.Error(msg)
            raise RuntimeError(msg)

        # Load S&P 500 PIT membership
        import csv
        from pathlib import Path
        sp500_csv = Path(__file__).resolve().parent / "data" / "sp500_ticker_start_end.csv"
        self.sp500_membership = {}
        if not sp500_csv.exists():
            raise RuntimeError(
                f"S&P 500 membership CSV not found at {sp500_csv} — "
                "run scripts/download_equity_data.py --refresh-sp500 or restore the file."
            )
        with open(sp500_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "ticker" not in [h.strip().lower() for h in reader.fieldnames]:
                raise RuntimeError(f"Invalid S&P membership CSV header: {reader.fieldnames}")
            # Normalize fieldnames to lower for BOM/case robustness
            for row in reader:
                try:
                    ticker = (row.get("ticker") or row.get("Ticker") or "").strip()
                    start = (row.get("start_date") or row.get("Start_date") or "").strip()
                    end_raw = (row.get("end_date") or row.get("End_date") or "")
                    end = end_raw.strip() or None
                except (AttributeError, KeyError) as e:
                    raise RuntimeError(f"Malformed row in {sp500_csv}: {row}") from e
                if not ticker:
                    continue
                if not start:
                    raise RuntimeError(f"Missing start_date for {ticker} in {sp500_csv}")
                self.sp500_membership.setdefault(ticker, []).append((start, end))
        if not self.sp500_membership:
            raise RuntimeError(f"S&P membership CSV empty: {sp500_csv}")

        # Bootstrap equity CSV.zip files from embedded bars so Lean's data feed
        # has bars (time loop only advances on data events).
        import os
        from data.bootstrap_data import bootstrap, _find_data_dirs
        cwd = os.getcwd()
        n_written = bootstrap(cwd)
        self.Log(f"DIAG cwd={cwd} data_dirs={[str(d) for d in _find_data_dirs(cwd)]} bootstrap_wrote={n_written}")

        # Subscriptions must be point-in-time: a name that joins the S&P 500
        # AFTER the backtest start must still become tradeable on its actual
        # membership start date (otherwise ~300 later additions are silently
        # never traded). But subscribing ALL members up front blows Lean's
        # 10s security-initialization limit, so we only subscribe the members
        # active as of the start date here, and dynamically AddEquity() each
        # remaining member on the day its membership begins (see
        # _ensure_subscribed / OnData). Entry (index add) and exit (membership
        # end / corporate action) are enforced in DailyRebalance.
        self._registered = []
        self._subscribed = set()  # every ticker we have ever AddEquity()'d
        # In Initialize, self.Time is not yet the backtest start — use the
        # configured window directly to avoid day-1 subscription drift.
        date_str = window["start"]
        skipped_no_bars = []
        for ticker, entries in self.sp500_membership.items():
            if ticker == "^TNX":
                continue
            if not entries:
                continue
            if ticker not in self.bars_cache:
                skipped_no_bars.append(ticker)
                continue
            # Only pre-subscribe members active as of the start date; later
            # additions are handled dynamically in _ensure_subscribed().
            if not intervals_active(entries, date_str):
                continue
            try:
                self.AddEquity(ticker, Resolution.Daily)
                self._registered.append(ticker)
                self._subscribed.add(ticker)
            except Exception as e:
                self.Log(f"WARN: failed to register {ticker}: {e}")
        if skipped_no_bars:
            self.Log(
                f"DIAG skipped {len(skipped_no_bars)} S&P500 members with no bars "
                f"(e.g. {', '.join(skipped_no_bars[:10])})"
            )

        self.trailing_stops = {}
        self.selected_symbols = set()
        self.sell_dates = {}  # symbol_str -> date_str when last liquidated (cooldown)
        self.last_rebalance_date = None
        self._symbols = {
            ticker: Symbol.Create(ticker, SecurityType.Equity, Market.USA)
            for ticker in self._registered
        }

        self.Log(
            f"PbRoeAtrAlgorithm initialized — {len(self._registered)} tickers, embedded data "
            f"VanTharp risk={self.risk_per_trade_pct:.2%} cap={self.max_position_pct:.0%} "
            f"atr={self.atr_period}x{self.atr_multiplier} SMA fine_selection={self.fine_selection_limit}"
        )

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
            try:
                o = float(bar["open"]); h = float(bar["high"])
                l = float(bar["low"]); c = float(bar["close"])
                v = float(bar.get("volume", 0) or 0)
            except (TypeError, ValueError, KeyError):
                continue
            if not all(math.isfinite(x) for x in (o, h, l, c, v)):
                continue
            if any(x <= 0 for x in (o, h, l, c)):
                continue
            trade = TradeBar()
            trade.Symbol = symbol
            trade.Time = self.Time
            trade.Open = o
            trade.High = h
            trade.Low = l
            trade.Close = c
            trade.Volume = v
            self.Securities[symbol].SetMarketPrice(trade)

    # ------------------------------------------------------------------
    # Rebalance trigger
    # ------------------------------------------------------------------
    def OnData(self, data):
        """Trigger daily rebalance on each new trading day."""
        today = self.Time.date()
        # Dynamically subscribe any S&P 500 member whose membership starts
        # today, so it becomes tradeable point-in-time (see Initialize note).
        # AddEquity is throttled by _subscribed so each name is added once.
        self._ensure_subscribed(self.Time.strftime("%Y-%m-%d"))
        if self.last_rebalance_date == today:
            return
        self.last_rebalance_date = today
        self.DailyRebalance()

    def _ensure_subscribed(self, date_str: str) -> None:
        """AddEquity() for any member whose membership begins on ``date_str``.

        Called every trading day from OnData. Only names we have bars for are
        subscribed (no-bars names stay in equity_unavailable). Each ticker is
        added at most once via ``self._subscribed``.
        """
        for ticker, entries in self.sp500_membership.items():
            if ticker in self._subscribed or ticker == "^TNX":
                continue
            if ticker not in self.bars_cache:
                continue
            # Subscribe only intervals that are actually active on ``date_str``.
            # ``intervals_active`` requires start <= today AND (end is None or
            # today <= end), so historical members that exited long before the
            # backtest never get subscribed (subscribing dead members would blow
            # Lean's subscription budget and re-introduce the day-1 spike).
            if intervals_active(entries, date_str):
                try:
                    self.AddEquity(ticker, Resolution.Daily)
                    self._registered.append(ticker)
                    self._subscribed.add(ticker)
                    self._symbols[ticker] = Symbol.Create(
                        ticker, SecurityType.Equity, Market.USA
                    )
                    self.Log(f"SUBSCRIBE {ticker} (index add {date_str})")
                except Exception as e:
                    self.Log(f"WARN: failed to subscribe {ticker}: {e}")

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

        # Ensure every security has a price before deciding exits (otherwise
        # Liquidate may fill at stale 0). Corporate actions and ATR stops both
        # need the correct bar close.
        self._ensure_prices()

        # Corporate-action exits (rename/merger/spinoff) — only stop+corporate liquidate
        exits = self._corporate_action_exits(date_str)
        for sym in list(self.selected_symbols):
            if sym in exits:
                self._liquidate_symbol(sym)

        # Check ATR stops on existing positions (cheap: prices + ATR only,
        # no fundamentals/beta regression)
        self._check_stops()

        # Expensive PIT screen (fundamental lookups + 252d beta regression)
        # No max_positions cap — portfolio size is risk/cash-constrained (whole shares).
        # Always run a FRESH point-in-time screen over the full fundamentals universe.
        # Filter to tickers that are actually S&P 500 members on this date
        # to prevent historical/future non-members from filling screen slots.
        pit_tickers = [
            t for t in self.fundamentals_history.keys()
            if intervals_active(self.sp500_membership.get(t, []), date_str)
        ] if self.sp500_membership else list(self.fundamentals_history.keys())
        selected = run_fine_selection(
            algorithm=self,
            tickers=pit_tickers,
            max_positions=self.fine_selection_limit,
            bars_cache=self.bars_cache,
            history_cache=self.fundamentals_history,
            market_bars=self.market_bars,
            erp_history_cache=self.erp_history_cache,
        )

        # Spec: only ATR stops (and corporate actions above) liquidate.
        # Empty screen (gap<=0 everywhere) means no new buys — preserve
        # current holdings and retry next day. ERP failures already emit
        # ERROR inside run_fine_selection; we just skip buys here.
        if not selected:
            self.Log(f"DailyRebalance {date_str}: empty screen (no gap>0) — no new buys, preserving {len(self.selected_symbols)} holdings")
            return

        # Never re-add a ticker that has a corporate-action exit today
        # (rename/merger/delisting or spinoff parent) within the same cycle.
        selected = [s for s in selected if s not in exits]

        if not selected:
            self.Log(f"DailyRebalance {date_str}: all screened tickers have corporate exits today — no new buys")
            return

        # Add new positions — risk/cash-constrained, no max_positions limit
        today = self.Time.date()
        added = set()
        try:
            remaining_cash = float(self.Portfolio.Cash)
        except (TypeError, ValueError):
            remaining_cash = 0.0
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
            # HasData is Lean's data-feed flag and stays False until the
            # next bar arrives, even though we inject today's price via
            # SetMarketPrice. Check the embedded bar directly to avoid a
            # 1-day entry lag on index-add names.
            has_bar = symbol is not None and self.bars_cache.get(symbol_str, {}).get(date_str) is not None
            if symbol is None or symbol not in self.Securities or not has_bar:
                self.Log(f"SKIP {symbol_str}: no price data yet")
                continue
            # Van Tharp ATR-risk sizing: position risks 1% of NAV, capped at 10% NAV (whole shares)
            nav = float(self.Portfolio.TotalPortfolioValue)
            if nav <= 0 or not math.isfinite(nav):
                try:
                    nav = float(self.Portfolio.Cash)
                except (TypeError, ValueError):
                    nav = 0.0
                if nav <= 0 or not math.isfinite(nav):
                    self.Log(f"SKIP {symbol_str}: invalid NAV nav={nav}")
                    continue
            # Entry price from injected Security.Price; explicit fallback with warning
            entry_price = 0.0
            try:
                if symbol is not None and symbol in self.Securities:
                    entry_price = float(self.Securities[symbol].Price)
            except (TypeError, ValueError, AttributeError):
                entry_price = 0.0
            if entry_price <= 0 or not math.isfinite(entry_price):
                bar_close = self.bars_cache.get(symbol_str, {}).get(date_str, {})
                try:
                    fallback_price = float(bar_close.get("close", 0) or 0)
                except (TypeError, ValueError):
                    fallback_price = 0.0
                if fallback_price > 0 and math.isfinite(fallback_price):
                    self.Log(f"FALLBACK {symbol_str}: Security.Price invalid, using embedded bar close={fallback_price:.2f}")
                    entry_price = fallback_price
                else:
                    entry_price = 0.0
            if entry_price <= 0 or not math.isfinite(entry_price):
                self.Log(f"SKIP {symbol_str}: no entry price")
                continue
            # Compute entry stop from identical bars (no ratchet yet)
            entry_stop = compute_atr_trailing_stop(
                symbol_str,
                self.bars_cache,
                self.atr_period,
                self.atr_multiplier,
                "SMA",
                as_of_date=date_str,
                prev_stop=None,
            )
            if entry_stop is None or entry_stop <= 0 or entry_stop >= entry_price:
                self.Log(f"SKIP {symbol_str}: no usable ATR stop entry={entry_price:.2f} stop={entry_stop}")
                continue
            risk_per_share = entry_price - entry_stop
            if risk_per_share <= 0 or not math.isfinite(risk_per_share):
                self.Log(f"SKIP {symbol_str}: invalid risk_per_share entry={entry_price:.2f} stop={entry_stop:.2f}")
                continue
            shares, capped_dollars, van_tharp_dollars, _ = _van_tharp_shares(
                nav, entry_price, entry_stop, self.risk_per_trade_pct, self.max_position_pct
            )
            capped_by = "cap" if capped_dollars < van_tharp_dollars - 1e-9 else "risk"
            if shares <= 0:
                self.Log(
                    f"SKIP {symbol_str}: VanTharp shares=0 capped=${capped_dollars:,.0f} "
                    f"van_tharp=${van_tharp_dollars:,.0f} risk_per_share=${risk_per_share:.2f}"
                )
                continue
            # Cash gate: Cash account rejects orders exceeding settled cash — track remaining_cash across loop
            cost = shares * entry_price
            if cost > remaining_cash:
                max_cash_shares = int(remaining_cash / entry_price) if entry_price > 0 else 0
                if max_cash_shares <= 0:
                    self.Log(f"SKIP {symbol_str}: insufficient cash remaining=${remaining_cash:,.0f} need=${cost:,.0f}")
                    continue
                self.Log(f"ADJUST {symbol_str}: cash cap shares {shares}->{max_cash_shares} remaining=${remaining_cash:,.0f}")
                shares = max_cash_shares
                cost = shares * entry_price
                if shares <= 0:
                    continue
            self.MarketOrder(symbol, shares)
            remaining_cash -= cost
            self.entry_prices[symbol_str] = entry_price
            self.trailing_stops[symbol_str] = entry_stop
            actual_risk = shares * risk_per_share / nav if nav > 0 else 0
            actual_weight = shares * entry_price / nav if nav > 0 else 0
            self.Log(
                f"Added {symbol_str} shares={shares} entry={entry_price:.2f} "
                f"stop={entry_stop:.2f} dist={risk_per_share/entry_price:.2%} "
                f"{capped_by} risk={actual_risk:.2%} weight={actual_weight:.2%}"
            )
            added.add(symbol_str)

        # Spec: only stops/corporate actions remove holdings — not valuation
        # turnover. So we keep existing holdings and just add new fills.
        # Un-fillable (no-data) picks do not pin the book because they
        # are simply not added (added set excludes them).
        self.selected_symbols = (self.selected_symbols | added)

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
                self.trailing_stops.pop(symbol_str, None)
                self.entry_prices.pop(symbol_str, None)
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
        self.entry_prices.pop(symbol_str, None)
        # Drop from the tracked set so the slot frees up and future re-screens
        # are not frozen by a phantom "full" book.
        self.selected_symbols.discard(symbol_str)

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
        # Consolidated EPS-growth coverage warning (one line per ticker, with
        # the as_of span it was missing) instead of per-rebalance spam.
        log_missing_g_eps_summary(self)
        invested = sum(
            1 for s in self.Portfolio.Keys if self.Portfolio[s].Invested
        )
        self.Log(f"Positions Held: {invested}")
        self.Log("=" * 60)
