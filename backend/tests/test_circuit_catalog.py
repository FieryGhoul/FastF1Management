from app.circuit_catalog import (
    CATALOG_REFERENCE,
    projected_points,
    svg_points,
    sync_catalog_maps,
    sync_f1db_metadata,
)
from app.mongo import database


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_geojson_catalog_fills_a_matching_missing_map():
    database.circuits.insert_one({
        "_id": "hungaroring", "name": "Hungaroring", "locality": "Budapest",
        "country": "Hungary", "map_data": None,
    })
    features = [{
        "type": "Feature",
        "properties": {
            "Name": "Hungaroring", "Location": "Budapest", "length": 4381,
            "firstgp": 1986,
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [[19.24, 47.57], [19.25, 47.58], [19.26, 47.57]],
        },
    }]

    result = sync_catalog_maps(database, features)
    circuit = database.circuits.find_one({"_id": "hungaroring"})

    assert result == {"matched": 1, "unmatched": 0, "catalog_features": 1}
    assert circuit["map_reference_session"] == CATALOG_REFERENCE
    assert circuit["length_km"] == 4.381
    assert len(circuit["map_data"]["points"]) == 3


def test_projection_rejects_incomplete_geometry():
    assert projected_points([[1, 2], [2, 3]]) == []


def test_catalog_rejects_a_generic_international_circuit_name_match():
    database.circuits.insert_one({
        "_id": "buddh", "name": "Buddh International Circuit",
        "locality": "Uttar Pradesh", "country": "India", "map_data": None,
    })
    features = [{
        "properties": {"id": "my-1999", "Name": "Sepang International Circuit", "Location": "Sepang"},
        "geometry": {"type": "LineString", "coordinates": [[1, 1], [2, 2], [3, 1]]},
    }]

    result = sync_catalog_maps(database, features)

    assert result["matched"] == 0
    assert result["unmatched"] == 1
    assert database.circuits.find_one({"_id": "buddh"})["map_data"] is None


def test_catalog_enriches_metadata_without_replacing_a_fastf1_map():
    original_map = {"points": [{"X": 0, "Y": 0}, {"X": 1, "Y": 1}, {"X": 2, "Y": 0}]}
    database.circuits.insert_one({
        "_id": "hungaroring",
        "name": "Hungaroring",
        "locality": "Budapest",
        "country": "Hungary",
        "map_data": original_map,
        "map_reference_session": "2025-14-Q",
    })
    features = [{
        "properties": {
            "id": "hu-1986", "Name": "Hungaroring", "Location": "Budapest",
            "length": 4381, "firstgp": 1986,
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [[19.24, 47.57], [19.25, 47.58], [19.26, 47.57]],
        },
    }]

    sync_catalog_maps(database, features)
    circuit = database.circuits.find_one({"_id": "hungaroring"})

    assert circuit["map_data"] == original_map
    assert circuit["map_reference_session"] == "2025-14-Q"
    assert circuit["first_grand_prix"] == 1986


def test_svg_curve_is_sampled_into_frontend_points():
    content = b'''<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 A10 10 0 0 1 20 0 Z" /></svg>'''
    points = svg_points(content, samples=20)
    assert len(points) == 21
    assert set(points[0]) == {"X", "Y"}


def test_f1db_metadata_uses_alias_and_preserves_full_record():
    database.circuits.insert_one({
        "_id": "albert-park",
        "name": "Albert Park Grand Prix Circuit",
        "country": "Australia",
    })
    metadata = {
        "melbourne": {
            "id": "melbourne",
            "name": "Melbourne",
            "type": "RACE",
            "direction": "CLOCKWISE",
            "length": 5.278,
            "turns": 14,
            "layouts": [{"id": "melbourne-1", "length": 5.278, "turns": 14}],
        },
    }

    result = sync_f1db_metadata(database, metadata)
    circuit = database.circuits.find_one({"_id": "albert-park"})

    assert result == {"matched": 1, "unmatched": 0}
    assert circuit["length_km"] == 5.278
    assert circuit["corner_count"] == 14
    assert circuit["circuit_type"] == "Race"
    assert circuit["direction"] == "Clockwise"
    assert circuit["circuit_metadata"]["layouts"][0]["id"] == "melbourne-1"
