# Frozen Shared Contract — Pipeline Throttling & Fundamentals Filter

Status: **LOCKED (M-tier).** Implementers MUST match these signatures/semantics
exactly. Any change requires re-freezing with the Boss. This is the single source
of truth that lets config / bars-filter / Tiingo-rotation tasks run in parallel.

## Why this exists
T1 (config keys), T2 (fundamentals filter helper), T3 (skip TTL), T5/T6/T7
(bars filters + Tiingo rotation) are built by different workers in parallel.
They share four contracts. Freeze first.

## Contract A — `config.get_tiingo_keys() -> List[str]`
- Module: `config/config.py` (already `import os`; already reads `TIINGO_API_KEY`).
- Sources (merge order; first occurrence wins for dedupe):
  1. `TIINGO_API_KEYS` — comma-separated; split on `,`, `.strip()` each, drop empties.
  2. `TIINGO_API_KEY` (legacy single)
  3. `TIINGO_API_KEY_1` (legacy)
  4. `TIINGO_API_KEY_2` (legacy)
- Dedupe: preserve first-seen order, exact (case-sensitive) string match.
- Returns `List[str]` of unique non-empty keys. Returns `[]` if none configured.
  Never raises on missing env.
- SECURITY: never log key values. Only allowed log:
  `f"Loaded {len(keys)} Tiingo API key(s)"`.
- Concrete example (current .env has duplicate first/_2):
  - env `TIINGO_API_KEYS="tiingo_key_placeholder_1,tiingo_key_placeholder_2"`
  - output `["tiingo_key_placeholder_1", "tiingo_key_placeholder_2"]` (2 unique).

## Contract B — `scripts.common.load_fundamentals_tickers(path=None) -> Set[str]`
- Module: `lean_project/scripts/common.py` (already imported by all three bars scripts).
- Default path: `Path(__file__).resolve().parent.parent / "data" / "fundamentals_history.json"`.
- Behavior:
  - Load JSON; return `set(data.keys())` (top-level ticker symbols).
  - Always include `REQUIRED_INDICES = {"^TNX", "^GSPC"}` (model breaks without them).
  - Return `set(fundamentals_keys) | REQUIRED_INDICES`.
  - If file missing / empty / invalid JSON: return `set(REQUIRED_INDICES)`
    (never raise; callers still fetch indices).
- Pure: no network, no `import config` side effects.

## Contract C — Skip-set TTL semantics (`download_edgartools_data.py`)
- Skip file: `fundamentals_no_edgar_match.json` (data dir). CIK map: `sp500_cik_map.csv`.
- `cik_map_mtime = mtime(cik_map_path)`; `skip_mtime = mtime(skip_path)` if exists.
- Rules (evaluated each run, before the ticker loop):
  1. `--clean-skip` flag: delete skip file at start. Log `f"--clean-skip: removed {skip_path}"`.
  2. Else if skip exists AND `skip_mtime >= cik_map_mtime`: respect skip set (current behavior).
  3. Else if skip exists AND `skip_mtime < cik_map_mtime`: CIK map grew since skip written
     → auto-retry. Log `f"CIK map updated since skip file; retrying {len(skip_set)} previously-skipped tickers"`.
     Do NOT skip; fall through to `resolve_company`.
  4. `--force` (existing): wipes `fundamentals_history.json` + re-downloads all; implies ignoring skip set.
- B1 fallback (already on disk at lines 417–428; VERIFY, do not regress):
  `get_quarterly_history` / `resolve_company` must not raise `AttributeError` when
  `Company` lacks `.ticker`. Use `company.get_ticker()` in try/except, then
  `getattr(company, "tickers", None)[0]`, then `str(getattr(company, "cik", "unknown"))`.

## Contract D — Tiingo rotating token iterator (consumed by `fetch_missing_delisted.py`)
- Helper `iter_tiingo_keys(keys: List[str])` yields keys round-robin, one per request.
- Per-request failover: on HTTP 429/403/503 for current key, advance to next key and
  retry same request; if all keys exhausted → raise `TiingoRateLimited` (DEFER — do NOT
  write to `equity_unavailable.json`).
- Keys never logged.
- If `config.get_tiingo_keys()` returns `[]`: behave exactly like today's "no token"
  path (skip Tiingo fallback, degrade to rename/unavailable).
