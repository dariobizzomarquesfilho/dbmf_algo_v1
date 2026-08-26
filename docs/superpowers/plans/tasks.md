# Tasks — Pipeline Throttling, Fundamentals Filter & Tiingo Rotation

Global: work in a git worktree/branch, **no commits**. Implement against the frozen
contract at `docs/superpowers/specs/shared-contract.md`.

## Concise task table
| ID | Title | Wave | Depends | Files |
|----|-------|------|---------|-------|
| T1 | `config.get_tiingo_keys()` + `TIINGO_API_KEYS` | W1 | C0 | config/config.py |
| T2 | `load_fundamentals_tickers()` helper | W1 | C0 | lean_project/scripts/common.py |
| T3 | edgartools `--clean-skip` + skip TTL + B1 verify | W1 | C0 | lean_project/scripts/download_edgartools_data.py |
| T4 | build_cik_map `--refresh` docs | W1 | — | lean_project/scripts/build_cik_map.py |
| T5 | download_equity_data filter + `--fundamentals-only` | W2 | T2 | lean_project/scripts/download_equity_data.py |
| T6 | repair_equity_data filter | W2 | T2 | lean_project/scripts/repair_equity_data.py |
| T7 | fetch_missing_delisted rotation + filter | W2 | T1,T2 | lean_project/scripts/fetch_missing_delisted.py |
| T8 | test_tiingo_rotation.py (NEW) | W3 | T1,T7 | lean_project/tests/test_tiingo_rotation.py |
| T9 | test_fundamentals_filter.py (NEW, +B1) | W3 | T2,T3,T5,T6,T7 | lean_project/tests/test_fundamentals_filter.py |
| T10 | embed validation scope to fundamentals universe | W4 | T2,T5,T6,T7 | lean_project/scripts/embed_data.py |
| T11 | full rebuild + pipeline dry-run (worktree, no commit) | W5 | T1–T10 | runbook |

---

### T1 — `config.get_tiingo_keys()` + `TIINGO_API_KEYS` parsing
- **Files:** `config/config.py`
- **Depends:** C0 (Contract A)
- **Steps:**
  1. Add `def get_tiingo_keys() -> list[str]` reading `TIINGO_API_KEYS` (comma-split, strip, drop empty) then legacy `TIINGO_API_KEY`, `TIINGO_API_KEY_1`, `TIINGO_API_KEY_2`.
  2. Merge in that order; dedupe preserving first-seen (case-sensitive exact).
  3. Return `[]` when none set; never raise on missing env.
  4. Only allowed log: `f"Loaded {len(keys)} Tiingo API key(s)"`.
- **Acceptance:**
  - `TIINGO_API_KEYS="k1,k2,k1"` → `["k1","k2"]`.
  - legacy `TIINGO_API_KEY=kA`, `_1=kB`, `_2=kA` → `["kA","kB"]`.
  - `TIINGO_API_KEYS="k1"` + `TIINGO_API_KEY=k1` → `["k1"]`.
  - none set → `[]`.
  - No key substring appears in any log/print.
- **Test plan:** T8 (`test_get_tiingo_keys_env_combos`, `test_get_tiingo_keys_no_log`) via `monkeypatch` of `os.getenv`.

### T2 — `load_fundamentals_tickers()` helper
- **Files:** `lean_project/scripts/common.py`
- **Depends:** C0 (Contract B)
- **Steps:** Add `REQUIRED_INDICES = {"^TNX", "^GSPC"}` and `def load_fundamentals_tickers(path=None) -> set`. Default path = `lean_project/data/fundamentals_history.json`. Return `set(keys) | REQUIRED_INDICES`; on missing/empty/invalid → `set(REQUIRED_INDICES)`. No network, no `import config`.
- **Acceptance:**
  - temp `{"AAPL":{},"MSFT":{}}` → `{"AAPL","MSFT","^TNX","^GSPC"}`.
  - missing file → `{"^TNX","^GSPC"}`.
  - `{}` → `{"^TNX","^GSPC"}`.
- **Test plan:** T9 (`test_load_fundamentals_tickers_union`, `test_load_fundamentals_tickers_missing_file`).

### T3 — edgartools `--clean-skip` + skip-set TTL + B1 verify
- **Files:** `lean_project/scripts/download_edgartools_data.py`
- **Depends:** C0 (Contract C)
- **Steps:**
  1. Add `--clean-skip` argparse flag; at start, if set and skip file exists, delete it (log).
  2. Compute `cik_map_mtime` / `skip_mtime`; apply TTL rule (auto-retry when cik map newer).
  3. Verify B1 fallback (lines 417–428) present & correct — no `AttributeError` path.
  4. Keep `--force` behavior (wipes history, re-downloads all, ignores skip).
- **Acceptance:**
  - Temp dir: skip file mtime older than freshly-written cik map → previously-skipped ticker is re-probed (`resolve_company` called for it).
  - `--clean-skip` removes skip file before loop.
  - Fake `Company` without `.ticker` does not raise `AttributeError` in parse-failure logging / `resolve_company`.
  - `--force` still re-downloads all.
- **Test plan:** T9 (`test_edgartools_skip_ttl_retry`, `test_b1_company_ticker_fallback`) using temp files + mocked `resolve_company`/`Company`.

### T4 — build_cik_map `--refresh` docs
- **Files:** `lean_project/scripts/build_cik_map.py`
- **Depends:** none
- **Steps:** Clarify `--refresh` help/docstring: rebuilds current-only map (merge=False) and should precede the fundamentals rebuild. No behavior change.
- **Acceptance:** `--help` shows clear note; `merge=False` path unchanged.
- **Test plan:** none (doc-only). Manual: `python scripts/build_cik_map.py --refresh` still writes map.

### T5 — download_equity_data filter + `--fundamentals-only`
- **Files:** `lean_project/scripts/download_equity_data.py`
- **Depends:** T2
- **Steps:**
  1. In `main()`, set `target = load_fundamentals_tickers()` and use it as the ticker list (replaces full S&P list; indices already included by contract).
  2. Add `--fundamentals-only` flag (accepted; default already strict). When present, log that strict filter is active.
  3. Keep `--tickers` explicit override (e.g. single-ticker test).
- **Acceptance:**
  - Without `--tickers`, fetched set == `load_fundamentals_tickers()` (≈678 + 2 indices), NOT full 1208 S&P list.
  - `^TNX`, `^GSPC` always present.
  - `--tickers AAPL MSFT` still works.
- **Test plan:** T9 (`test_download_uses_filtered_list`) — monkeypatch `yf.download`, capture ticker args.

### T6 — repair_equity_data filter
- **Files:** `lean_project/scripts/repair_equity_data.py`
- **Depends:** T2
- **Steps:** Replace `requested = sorted(membership.keys()) + ["^TNX","^GSPC"]` with `requested = sorted(load_fundamentals_tickers())`.
- **Acceptance:** `run_repair` only recovers fundamentals-universe tickers (+indices); a historical non-fundamentals member is never in `requested`.
- **Test plan:** T9 (`test_repair_requested_filtered`) — monkeypatch `_fetch_with_variants`, assert `requested` set.

### T7 — fetch_missing_delisted rotation + filter
- **Files:** `lean_project/scripts/fetch_missing_delisted.py`
- **Depends:** T1, T2, Contract D
- **Steps:**
  1. Add `iter_tiingo_keys(keys)` rotating iterator; replace single `token` in `recover_gaps` with iterator; on 429/403/503 advance key; exhausted → `TiingoRateLimited` (defer).
  2. `compute_missing` restricted to `load_fundamentals_tickers()` (never recover bars for non-fundamentals tickers). Keep indices excluded from "missing" (already skipped).
  3. `main()` builds keys via `config.get_tiingo_keys()`; empty → keep no-token path.
- **Acceptance:**
  - 2 keys, 3 requests → cycle k1,k2,k1.
  - Simulated 429 on k1 → retries on k2; both 429 → `TiingoRateLimited` raised (deferred, not unavailable).
  - `compute_missing` over bars missing a non-fundamentals ticker → that ticker not in missing.
  - No key logged.
- **Test plan:** T8 (`test_rotation_cycles_keys`, `test_failover_on_429`, `test_exhausted_raises`); T9 (`test_fetch_compute_missing_filtered`).

### T8 — test_tiingo_rotation.py (NEW)
- **Files:** `lean_project/tests/test_tiingo_rotation.py`
- **Depends:** T1, T7
- **Content:** `test_get_tiingo_keys_env_combos` (parametrized), `test_get_tiingo_keys_no_log` (caplog), `test_rotation_cycles_keys`, `test_failover_on_429`, `test_exhausted_raises`. Mock `_tiingo_request` for rotation/failover.
- **Acceptance:** all pass; covers Contract A + D.

### T9 — test_fundamentals_filter.py (NEW, +B1)
- **Files:** `lean_project/tests/test_fundamentals_filter.py`
- **Depends:** T2, T3, T5, T6, T7
- **Content:** `test_load_fundamentals_tickers_union`, `test_load_fundamentals_tickers_missing_file`, `test_download_uses_filtered_list`, `test_repair_requested_filtered`, `test_fetch_compute_missing_filtered`, `test_b1_company_ticker_fallback`, `test_edgartools_skip_ttl_retry`.
- **Acceptance:** all pass; covers Contract B + C + filter application + B1 regression.

### T10 — embed validation scope to fundamentals universe (design pt 4)
- **Files:** `lean_project/scripts/embed_data.py` (`validate_data_coverage`)
- **Depends:** T2, T5, T6, T7
- **Steps:** Load fundamentals keys; in the per-current-member loop, only evaluate a current member if it is in `fundamentals_history.json` keys (the tradeable universe). Current members absent from fundamentals are not required to have bars (they are not fetched by design).
- **Acceptance:**
  - After clean rebuild+filter, `validate_data_coverage` returns `ok=True` even if a current member lacks fundamentals (not in tradeable universe).
  - A current member WITH fundamentals but missing/incomplete bars is still flagged (`ok=False`) — correct.
  - Equity bars hard-fail checks (span/end) unchanged.
- **Test plan:** extend `test_embed.py` or manual run after T11; assert `ok=True` on filtered data.

### T11 — full rebuild + pipeline dry-run (worktree, no commit)
- **Files:** none new (runbook)
- **Depends:** T1–T10
- **Steps (in a git worktree/branch, NO commit):**
  1. `cd lean_project && python scripts/build_cik_map.py --refresh`
  2. delete `data/fundamentals_no_edgar_match.json`
  3. `python scripts/download_edgartools_data.py --force` (recovers AVB/EA/EQR/FOX/FOXA/IR if possible)
  4. `python scripts/download_equity_data.py` (filtered)
  5. `python scripts/repair_equity_data.py` (filtered)
  6. `python scripts/fetch_missing_delisted.py --apply` (rotating keys + filtered)
  7. `python scripts/embed_data.py`
  8. `python -m pytest lean_project/tests implied_erp/tests`
- **Acceptance:**
  - Pipeline completes without hard-fail.
  - Embed validation `ok=True` (warning-only, passes).
  - `equity_bars.json` ≈ 678 + 2 indices (not 1208).
  - `get_tiingo_keys()` returns 2 unique keys; no 429 cascade in logs.
  - Tests green.
  - **NO git commit** (worktree only).
