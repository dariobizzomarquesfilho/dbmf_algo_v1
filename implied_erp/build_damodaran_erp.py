"""Gera ``src/data/damodaran_erp.json`` a partir da planilha de country risk
premiums do Damodaran (``ctryprem*.xlsx``).

A planilha é um download pontual do Damodaran e não deve ser dependência de
runtime. Este script faz a extração *automática* uma única vez e salva um JSON
versionável que o módulo ``src/screener/damodaran.py`` consome.

Usa o extrator layout-robust de ``implied_erp/extract_damodaran_erp.py`` para
manter consistência com a extração de arquivos antigos (ctrypremNN.xls).

Rode de novo sempre que o Damodaran publicar uma planilha nova::

    python scripts/build_damodaran_erp.py
    python scripts/build_damodaran_erp.py --xlsx caminho/ctryprem.xlsx --out src/data/damodaran_erp.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Repo root = dois níveis acima deste script (implied_erp/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from implied_erp.extract_damodaran_erp import extract as _extract_sheet  # noqa: E402

DEFAULT_XLSX = REPO_ROOT / "implied_erp" / "data" / "ctryprem.xlsx"
DEFAULT_OUT = REPO_ROOT / "implied_erp" / "data" / "damodaran_erp.json"


def extract(xlsx_path: str) -> dict:
    """Extrai país -> ERP via o extrator robusto compartilhado."""
    data = _extract_sheet(xlsx_path)

    countries: dict[str, float] = {}
    for name, entry in data.get("countries", {}).items():
        erp = entry.get("total_equity_risk_premium2") or entry.get("total_equity_risk_premium")
        if erp is None:
            continue
        countries[name] = float(erp)

    if not countries:
        raise ValueError(f"Nenhum país encontrado em {xlsx_path}")

    return {
        "source": data.get("source"),
        "updated": data.get("updated"),
        "mature_market_erp": data.get("mature_market_erp")
        if data.get("mature_market_erp") is not None else 0.042,
        "countries": countries,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Gera o JSON de ERP do Damodaran.")
    ap.add_argument("--xlsx", default=str(DEFAULT_XLSX), help="Caminho da planilha ctryprem*.xlsx")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Caminho do JSON de saída")
    ap.add_argument("--report", action="store_true", help="Imprimir relatório de layout")
    args = ap.parse_args()

    data = extract(args.xlsx)
    if args.report:
        from implied_erp.extract_damodaran_erp import build_layout_report
        print(build_layout_report(_extract_sheet(args.xlsx)), file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Origem : {data['source']}")
    print(f"Países : {len(data['countries'])}")
    print(f"Mature : {data['mature_market_erp']}")
    print(f"Salvo  : {out_path}")


if __name__ == "__main__":
    main()
