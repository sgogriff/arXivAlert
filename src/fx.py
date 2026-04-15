"""Lightweight FX helpers for approximate cost display."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class FxRates:
    usd_to_gbp: float
    usd_to_eur: float
    date: str = ""
    source: str = ""


_FALLBACK = FxRates(
    usd_to_gbp=0.74,
    usd_to_eur=0.92,
    date="",
    source="fallback",
)


def fetch_rates(timeout_s: float = 3.0) -> FxRates:
    """Fetch USD->GBP/EUR rates.

    Uses Frankfurter (ECB-backed) when available; falls back to a static estimate.
    """
    url = "https://api.frankfurter.app/latest"
    try:
        r = httpx.get(
            url,
            params={"from": "USD", "to": "GBP,EUR"},
            timeout=timeout_s,
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json()
        rates = data.get("rates") or {}
        gbp = float(rates["GBP"])
        eur = float(rates["EUR"])
        date = str(data.get("date") or "")
        return FxRates(usd_to_gbp=gbp, usd_to_eur=eur, date=date, source="frankfurter")
    except Exception:
        return _FALLBACK


def convert_usd(usd: float, rates: FxRates) -> tuple[float, float, float]:
    return (usd * rates.usd_to_gbp, usd * rates.usd_to_eur, usd)
