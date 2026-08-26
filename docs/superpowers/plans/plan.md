# Plan — Pipeline Throttling, Fundamentals Filter & Tiingo Key Rotation

> For agentic workers: implement task-by-task per `tasks.md`. Shared contract
> (`docs/superpowers/specs/shared-contract.md`) is LOCKED — implement against it.

## Goal
Fix four pipeline defects without changing the trading model:
- **B1** (`AttributeError Company.ticker`) — already fixed on disk; add verification + regression.
- **W1/W2** (Yahoo/Tiingo 429 throttling, incomplete bars) — Tiingo multi-key rotation w/ per-key 429 failover; cut wasted quota.
- **W3** (embed validation missing CURRENT members AVB/EA/EQR/FOX/FOXA/IR warm-up violations) — filter all bars scripts to fundamentals universe ∪ {^TNX,^GSPC}; reconcile embed validation.
- **Waste** (1208 equity fetches vs 678 fundamentals) — restrict bars fetches to the fundamentals universe (~530 fewer requests).

## Architecture
- `config.get_tiingo_keys()` = single source of Tiingo credentials (replaces ad-hoc `config.TIINGO_API_KEY`).
- `scripts.common.load_fundamentals_tickers()` = single source of the tradeable universe (fundamentals keys + required indices).
- All three bars scripts import the same helper → identical filtering, no drift.
- `download_edgartools_data` gains `--clean-skip` + skip-set TTL so a refreshed CIK map auto-recovers previously-skipped (delisted) tickers.
- Tiingo requests rotate across keys with 429 failover; exhausted quota → defer (never mark unavailable).

## Global Constraints
- Python 3.11+, repo `.venv`. **No commits** — work in a git worktree/branch.
- No trading-model changes; indices (^TNX, ^GSPC) always fetched.
- No key logging anywhere.
- Tests: pytest only (no linter). `conftest.py` already injects paths.

## File Structure (changes)
| File | Change |
|------|--------|
| `config/config.py` | Add `get_tiingo_keys()` + `TIINGO_API_KEYS` parsing |
| `lean_project/scripts/common.py` | Add `load_fundamentals_tickers()` (+ `REQUIRED_INDICES`) |
| `lean_project/scripts/download_edgartools_data.py` | Verify B1; add `--clean-skip`; skip-set TTL |
| `lean_project/scripts/build_cik_map.py` | `--refresh` doc clarity (trivial) |
| `lean_project/scripts/download_equity_data.py` | Fundamentals filter + `--fundamentals-only` flag |
| `lean_project/scripts/repair_equity_data.py` | Same filter on `requested` |
| `lean_project/scripts/fetch_missing_delisted.py` | Tiingo rotation + fundamentals filter |
| `lean_project/scripts/embed_data.py` | Scope per-current-member check to fundamentals universe (design pt 4) |
| `lean_project/tests/test_fundamentals_filter.py` | NEW — filter + B1 regression |
| `lean_project/tests/test_tiingo_rotation.py` | NEW — key merge/dedupe + rotation/failover |

## Frozen Shared Contract (summary)
See `docs/superpowers/specs/shared-contract.md`. Locks:
- `config.get_tiingo_keys() -> List[str]` (merge TIINGO_API_KEYS + legacy, dedupe, no logging).
- `scripts.common.load_fundamentals_tickers() -> Set[str]` (fundamentals keys ∪ {^TNX,^GSPC}; indices-only fallback).
- Skip-set TTL: `--clean-skip` deletes skip file; if CIK map mtime > skip mtime, auto-retry skipped.
- Tiingo iterator: round-robin + per-key 429 failover; exhausted → `TiingoRateLimited` (defer).

## Task Dependency Graph
```
C0 Contract (planner) ──┬──▶ T1 config.get_tiingo_keys()
                        ├──▶ T2 load_fundamentals_tickers()
                        ├──▶ T3 edgartools --clean-skip + TTL (+B1 verify)
                        └──▶ T4 build_cik_map --refresh docs
T1 ─────────────┐
T2 ────┬────────┼──▶ T5 download_equity filter
       │        ├──▶ T6 repair_equity filter
       └────────┴──▶ T7 fetch_missing rotation + filter
T1,T7 ─────────────▶ T8 test_tiingo_rotation.py
T2,T5,T6,T7,T3 ────▶ T9 test_fundamentals_filter.py (+B1)
T2,T5,T6,T7 ───────▶ T10 embed validation scope (design pt 4)
T1..T10 ───────────▶ T11 full rebuild + pipeline dry-run (worktree, no commit)
```
Parallel waves: W1={T1,T2,T3,T4}; W2={T5,T6,T7}; W3={T8,T9}; W4={T10}; W5={T11}.

## Verification (end state)
- `pytest lean_project/tests` green (incl. new T8, T9).
- `python -c "import config; print(config.get_tiingo_keys())"` → 2 unique keys, no values logged.
- Full rebuild in worktree: `build_cik_map.py --refresh` → delete `fundamentals_no_edgar_match.json`
  → `download_edgartools_data.py --force` → `download_equity_data.py` (filtered)
  → `repair_equity_data.py` (filtered) → `fetch_missing_delisted.py --apply` (rotating) → `embed_data.py`.
- Embed validation returns `ok=True` (warning-only, now passes). `equity_bars.json` ≈ 678 + 2 indices. No 429 cascade.
