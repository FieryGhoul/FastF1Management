from typing import Any

from rapidfuzz import fuzz


COUNTRY_GROUPS = (
    frozenset({"UK", "United Kingdom", "Great Britain"}),
    frozenset({"USA", "United States", "United States of America"}),
    frozenset({"UAE", "United Arab Emirates"}),
)


def country_variants(country: str | None) -> list[str]:
    if not country:
        return []
    return list(next((group for group in COUNTRY_GROUPS if country in group), {country}))


def circuit_match_score(circuit: dict[str, Any], target: str) -> float:
    return max(
        fuzz.token_set_ratio(f"{circuit.get('name', '')} {circuit.get('locality', '')}", target),
        fuzz.partial_ratio(circuit.get("name", ""), target),
        fuzz.token_set_ratio(circuit.get("locality", ""), target),
    )
