"""Canonical circuit outlines for tracks without supported FastF1 position data."""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError
from typing import Any
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from pymongo.database import Database
from svg.path import parse_path
import yaml

from .circuit_matching import circuit_match_score
from .mongo import utcnow


CATALOG_URL = "https://raw.githubusercontent.com/bacinger/f1-circuits/master/f1-circuits.geojson"
CATALOG_REFERENCE = "geojson:bacinger/f1-circuits"
F1DB_BASE_URL = "https://raw.githubusercontent.com/f1db/f1db/main/src/assets/circuits/black"
F1DB_REFERENCE = "svg:f1db/f1db"
F1DB_DATA_BASE_URL = "https://raw.githubusercontent.com/f1db/f1db/main/src/data/circuits"
F1DB_METADATA_REFERENCE = "yaml:f1db/f1db"
F1DB_ID_ALIASES = {
    "albert-park": "melbourne",
    "americas": "austin",
    "losail": "lusail",
    "red-bull-ring": "spielberg",
    "ricard": "paul-ricard",
    "rodriguez": "mexico-city",
    "spa": "spa-francorchamps",
    "vegas": "las-vegas",
    "villeneuve": "montreal",
}
FIRST_GRAND_PRIX_FALLBACKS = {
    "albert-park": 1996,
    "fuji": 1976,
    "hungaroring": 1986,
    "marina-bay": 2008,
    "miami": 2022,
    "shanghai": 2004,
    "suzuka": 1987,
}


def fetch_catalog() -> list[dict[str, Any]]:
    request = Request(
        CATALOG_URL,
        headers={"User-Agent": "RaceDataManagement/1.0 (circuit outline ingestion)"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed trusted URL
        payload = json.load(response)
    return payload.get("features", [])


def fetch_f1db_metadata(identifier: str) -> tuple[dict[str, Any], str]:
    url = f"{F1DB_DATA_BASE_URL}/{identifier}.yml"
    request = Request(
        url,
        headers={"User-Agent": "RaceDataManagement/1.0 (circuit metadata ingestion)"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed trusted base URL
        return yaml.safe_load(response.read()) or {}, url


def sync_f1db_metadata(
    db: Database,
    metadata_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Store full canonical F1DB metadata for every known circuit."""
    matched = 0
    unmatched = 0
    for circuit in db.circuits.find({}):
        identifier = F1DB_ID_ALIASES.get(circuit["_id"], circuit["_id"])
        url = f"{F1DB_DATA_BASE_URL}/{identifier}.yml"
        if metadata_by_id is not None:
            metadata = metadata_by_id.get(identifier)
        else:
            try:
                metadata, url = fetch_f1db_metadata(identifier)
            except HTTPError as exc:
                if exc.code == 404:
                    unmatched += 1
                    continue
                raise
        if not metadata:
            unmatched += 1
            continue
        db.circuits.update_one(
            {"_id": circuit["_id"]},
            {"$set": {
                "length_km": metadata.get("length") or circuit.get("length_km"),
                "corner_count": metadata.get("turns"),
                "circuit_type": str(metadata.get("type", "")).replace("_", " ").title() or None,
                "direction": str(metadata.get("direction", "")).replace("_", " ").title() or None,
                "circuit_metadata": metadata,
                "first_grand_prix": (
                    circuit.get("first_grand_prix")
                    or FIRST_GRAND_PRIX_FALLBACKS.get(circuit["_id"])
                ),
                "metadata_source_url": url,
                "metadata_source_attribution": "f1db/f1db",
                "metadata_reference": F1DB_METADATA_REFERENCE,
                "updated_at": utcnow(),
            }},
        )
        matched += 1
    return {"matched": matched, "unmatched": unmatched}


def projected_points(coordinates: list[list[float]]) -> list[dict[str, float]]:
    """Convert GeoJSON longitude/latitude pairs to local metric X/Y points."""
    valid = [pair for pair in coordinates if len(pair) >= 2]
    if len(valid) < 3:
        return []
    longitude = sum(float(pair[0]) for pair in valid) / len(valid)
    latitude = sum(float(pair[1]) for pair in valid) / len(valid)
    longitude_scale = 111_320 * math.cos(math.radians(latitude))
    return [
        {
            "X": round((float(pair[0]) - longitude) * longitude_scale, 3),
            "Y": round((float(pair[1]) - latitude) * 110_540, 3),
        }
        for pair in valid
    ]


def sync_catalog_maps(db: Database, features: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """Fill missing canonical maps from the maintained MIT GeoJSON catalog."""
    features = fetch_catalog() if features is None else features
    candidates = []
    for feature in features:
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "LineString":
            continue
        points = projected_points(geometry.get("coordinates", []))
        if points:
            candidates.append((properties, points))

    matched = 0
    unmatched = 0
    for circuit in db.circuits.find({}):
        ranked = sorted(
            (
                (
                    circuit_match_score(
                        circuit,
                        f"{properties.get('Name', '')} {properties.get('Location', '')}",
                    ),
                    properties,
                    points,
                )
                for properties, points in candidates
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        # Circuit names contain many generic tokens ("International",
        # "Circuit", "Street") that can otherwise produce convincing but
        # incorrect cross-country matches.  Catalog identities used here all
        # resolve at 100 through their proper name or locality aliases.
        if not ranked or ranked[0][0] < 95:
            unmatched += 1
            continue
        score, properties, points = ranked[0]
        fields = {
            "map_catalog_id": properties.get("id"),
            "map_catalog_name": properties.get("Name"),
            "map_match_score": round(score, 2),
            "length_km": circuit.get("length_km") or (
                float(properties["length"]) / 1000 if properties.get("length") else None
            ),
            "first_grand_prix": circuit.get("first_grand_prix") or properties.get("firstgp"),
            "updated_at": utcnow(),
        }
        if not circuit.get("map_data") or circuit.get("map_reference_session") == CATALOG_REFERENCE:
            fields.update({
                "map_data": {"points": points, "rotation": 0, "corners": []},
                "map_reference_session": CATALOG_REFERENCE,
                "map_source_url": CATALOG_URL,
                "map_source_attribution": "bacinger/f1-circuits (MIT)",
            })
        db.circuits.update_one({"_id": circuit["_id"]}, {"$set": fields})
        matched += 1
    return {"matched": matched, "unmatched": unmatched, "catalog_features": len(candidates)}


def svg_points(content: bytes, *, samples: int = 600) -> list[dict[str, float]]:
    root = ElementTree.fromstring(content)
    element = root.find("{http://www.w3.org/2000/svg}path")
    if element is None or not element.get("d"):
        return []
    path = parse_path(element.get("d"))
    points = []
    for index in range(samples + 1):
        point = path.point(index / samples)
        points.append({"X": round(point.real, 3), "Y": round(-point.imag, 3)})
    return points


def sync_f1db_maps(db: Database) -> dict[str, int]:
    """Fill remaining maps from F1DB's comprehensive MIT SVG archive."""
    matched = 0
    unmatched = 0
    for circuit in db.circuits.find({"$or": [{"map_data": None}, {"map_data": {"$exists": False}}]}):
        identifier = str(circuit.get("external_id") or circuit["_id"]).replace("_", "-")
        url = f"{F1DB_BASE_URL}/{identifier}-1.svg"
        request = Request(url, headers={"User-Agent": "RaceDataManagement/1.0 (circuit outline ingestion)"})
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed trusted base URL
                points = svg_points(response.read())
        except HTTPError as exc:
            if exc.code == 404:
                unmatched += 1
                continue
            raise
        if not points:
            unmatched += 1
            continue
        db.circuits.update_one(
            {"_id": circuit["_id"]},
            {"$set": {
                "map_data": {"points": points, "rotation": 0, "corners": []},
                "map_reference_session": F1DB_REFERENCE,
                "map_source_url": url,
                "map_source_attribution": "f1db/f1db (MIT)",
                "map_catalog_id": f"{identifier}-1",
                "map_catalog_name": circuit.get("name"),
                "map_match_score": 100,
                "updated_at": utcnow(),
            }},
        )
        matched += 1
    return {"matched": matched, "unmatched": unmatched}
