import importlib.util
import json
from pathlib import Path

from scripts.embed_data import embed_json


def test_embed_json_round_trip(tmp_path):
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"AAPL": {"2023-03-31": {"book_value": 3.92, "roe": 1.5, "eps": 6.1}}}))
    py = tmp_path / "out.py"
    embed_json(str(src), "FUNDAMENTALS_HISTORY", str(py))
    spec = importlib.util.spec_from_file_location("out", py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.load_fundamentals_history() == {"AAPL": {"2023-03-31": {"book_value": 3.92, "roe": 1.5, "eps": 6.1}}}
