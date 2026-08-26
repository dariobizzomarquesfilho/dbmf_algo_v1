"""Tests for Tiingo key resolution + round-robin rotation (T7/T8)."""

from __future__ import annotations

import pytest


def _load_config():
    import config

    return config


@pytest.mark.parametrize(
    "env,expected",
    [
        # TIINGO_API_KEYS (comma list) wins, legacy fields empty
        (
            {"TIINGO_API_KEYS": "k1,k2", "TIINGO_API_KEY": None,
             "TIINGO_API_KEY_1": None, "TIINGO_API_KEY_2": None},
            ["k1", "k2"],
        ),
        # legacy single key only
        (
            {"TIINGO_API_KEYS": None, "TIINGO_API_KEY": "legacy",
             "TIINGO_API_KEY_1": None, "TIINGO_API_KEY_2": None},
            ["legacy"],
        ),
        # comma list is stripped + de-duplicated preserving first-seen
        (
            {"TIINGO_API_KEYS": "a, b , a", "TIINGO_API_KEY": None,
             "TIINGO_API_KEY_1": None, "TIINGO_API_KEY_2": None},
            ["a", "b"],
        ),
        # numbered legacy keys
        (
            {"TIINGO_API_KEYS": None, "TIINGO_API_KEY": None,
             "TIINGO_API_KEY_1": "k1", "TIINGO_API_KEY_2": "k2"},
            ["k1", "k2"],
        ),
        # nothing set -> empty list
        (
            {"TIINGO_API_KEYS": "", "TIINGO_API_KEY": None,
             "TIINGO_API_KEY_1": None, "TIINGO_API_KEY_2": None},
            [],
        ),
        # de-duplicated across sources (case-sensitive)
        (
            {"TIINGO_API_KEYS": "x", "TIINGO_API_KEY": "x",
             "TIINGO_API_KEY_1": None, "TIINGO_API_KEY_2": None},
            ["x"],
        ),
    ],
)
def test_get_tiingo_keys_env_combos(config_env, monkeypatch, env, expected):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    config = _load_config()
    assert config.get_tiingo_keys() == expected


def test_get_tiingo_keys_no_log(config_env, monkeypatch, caplog):
    """Key *values* must never be logged; only the count is reported."""
    monkeypatch.setenv("TIINGO_API_KEYS", "secretkey123,another456")
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.delenv("TIINGO_API_KEY_1", raising=False)
    monkeypatch.delenv("TIINGO_API_KEY_2", raising=False)
    config = _load_config()
    with caplog.at_level("INFO"):
        keys = config.get_tiingo_keys()
    assert keys == ["secretkey123", "another456"]
    assert "secretkey123" not in caplog.text
    assert "another456" not in caplog.text
    assert "Loaded 2 Tiingo API key(s)" in caplog.text


def test_rotation_cycles_keys(config_env):
    from scripts.fetch_missing_delisted import _iter_tiingo_keys

    it = _iter_tiingo_keys(["a", "b", "c"])
    out = [next(it) for _ in range(7)]
    assert out == ["a", "b", "c", "a", "b", "c", "a"]
    # empty pool yields nothing
    assert list(_iter_tiingo_keys([])) == []


def test_failover_on_429(config_env, monkeypatch):
    """First key rate-limited -> next key is tried and succeeds."""
    import scripts.fetch_missing_delisted as m

    keys = ["bad", "good"]
    key_iter = m._iter_tiingo_keys(keys)

    def fake_request(path, token, ctx):
        if token == "bad":
            raise m.TiingoRateLimited("429")
        if "/prices" in path:
            return [{"date": "2020-01-02", "adjClose": 1.0}]
        return None  # metadata None -> accepted (valid US history)

    monkeypatch.setattr(m, "_tiingo_request", fake_request)
    bars, reason = m._tiingo_fetch(
        "AAPL", "2020-01-01", "2020-02-01", keys, None, key_iter
    )
    assert bars is not None
    assert reason is None


def test_exhausted_raises(config_env, monkeypatch):
    """All keys rate-limited -> TiingoRateLimited propagates (caller defers)."""
    import scripts.fetch_missing_delisted as m

    keys = ["bad1", "bad2"]
    key_iter = m._iter_tiingo_keys(keys)

    def fake_request(path, token, ctx):
        raise m.TiingoRateLimited("429")

    monkeypatch.setattr(m, "_tiingo_request", fake_request)
    with pytest.raises(m.TiingoRateLimited):
        m._tiingo_fetch("AAPL", "2020-01-01", "2020-02-01", keys, None, key_iter)
