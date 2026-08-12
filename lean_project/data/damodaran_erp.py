"""Custom data: Damodaran Equity Risk Premium by country.

Uses embedded data (damodaran_erp_json.py) — no external JSON file needed.
"""

from __future__ import annotations

from AlgorithmImports import *

from data.damodaran_erp_json import load_damodaran_erp as _load_erp


class DamodaranERP(PythonData):
    """Maps country name to equity risk premium (ERP) value."""

    def GetSource(self, config, date, isLive):
        return SubscriptionDataSource(
            "", SubscriptionTransportMedium.LocalFile,
        )

    def Reader(self, config, line, date, isLive):
        return None

    @staticmethod
    def load_cache() -> dict:
        """Load the full ERP data from embedded module."""
        return _load_erp()

    @staticmethod
    def get_erp(country: str = "United States") -> float:
        """Get ERP for a specific country. Falls back to US ERP, then 5.5%."""
        cache = DamodaranERP.load_cache()
        # Metadata format: {us_erp, mature_market_erp, countries: {name: {...}}}
        if country == "United States" and isinstance(cache.get("us_erp"), (int, float)):
            return float(cache["us_erp"])
        countries = cache.get("countries", {})
        cd = countries.get(country, {})
        for key in ("total_equity_risk_premium", "Total Equity Risk Premium", "TotalEquityRiskPremium", "ERP", "erp"):
            val = cd.get(key)
            if val is not None and isinstance(val, (int, float)):
                return float(val)
        if isinstance(cache.get("us_erp"), (int, float)):
            return float(cache["us_erp"])
        if isinstance(cache.get("mature_market_erp"), (int, float)):
            return float(cache["mature_market_erp"])
        return 0.055
