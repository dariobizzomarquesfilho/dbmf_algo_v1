"""Gera ``src/data/damodaran_erp.json`` a partir da planilha de country risk
premiums do Damodaran (``ctryprem*.xlsx``).

A planilha é um download pontual do Damodaran e não deve ser dependência de
runtime. Este script faz a extração *automática* uma única vez: lê a aba
"ERPs by country", extrai o par país -> "Total Equity Risk Premium" (coluna 5)
e salva um JSON versionável que o módulo ``src/screener/damodaran.py`` consome.

Rode de novo sempre que o Damodaran publicar uma planilha nova::

    python scripts/build_damodaran_erp.py
    python scripts/build_damodaran_erp.py --xlsx caminho/ctryprem.xlsx --out src/data/damodaran_erp.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import openpyxl

# Célula que traz o ERP de um mercado maduro (usado como fallback).
MATURE_MARKET_LABEL = "Enter the current risk premium for a mature equity market"
SHEET_NAME = "ERPs by country"
COUNTRY_COL = 1          # coluna A -> país
ERP_COL = 5              # coluna E -> "Total Equity Risk Premium"
HEADER_LABEL = "Country"

# Caminho default da planilha baixada (Downloads do usuário).
# Repo root = dois níveis acima deste script (scripts/ -> pb_roe/).
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XLSX = REPO_ROOT / "implied_erp" / "data" / "ctryprem.xlsx"
DEFAULT_OUT = REPO_ROOT / "implied_erp" / "data" / "damodaran_erp.json"


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract(xlsx_path: str) -> dict:
    """Lê a planilha e devolve o dicionário de metadados + países."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb[SHEET_NAME]

    mature_market_erp: float | None = None
    countries: dict[str, float] = {}
    in_data = False

    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        first = row[0]

        # Linha de cabeçalho: marca o início dos dados de país.
        if first == HEADER_LABEL:
            in_data = True
            continue

        # Antes dos dados, captura o ERP de mercado maduro (aparece acima).
        if not in_data and isinstance(first, str) and MATURE_MARKET_LABEL in first:
            mature_market_erp = _to_float(row[ERP_COL - 1])
            continue

        if not in_data:
            continue

        # Linhas de dados: país na coluna 1, ERP na coluna 5.
        country = first
        if not isinstance(country, str) or not country.strip():
            continue
        erp = _to_float(row[ERP_COL - 1] if len(row) >= ERP_COL else None)
        if erp is None:
            continue
        countries[country.strip()] = erp

    if not countries:
        raise ValueError(f"Nenhum país encontrado na aba '{SHEET_NAME}' de {xlsx_path}")

    return {
        "source": Path(xlsx_path).name,
        "updated": Path(xlsx_path).stem,
        "mature_market_erp": mature_market_erp if mature_market_erp is not None else 0.042,
        "countries": countries,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Gera o JSON de ERP do Damodaran.")
    ap.add_argument("--xlsx", default=DEFAULT_XLSX, help="Caminho da planilha ctryprem*.xlsx")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Caminho do JSON de saída")
    args = ap.parse_args()

    data = extract(args.xlsx)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Origem : {data['source']}")
    print(f"Países : {len(data['countries'])}")
    print(f"Mature : {data['mature_market_erp']}")
    print(f"Salvo  : {out_path}")


if __name__ == "__main__":
    main()
