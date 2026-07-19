from pathlib import Path

from app.fastf1_adapter import FastF1Adapter, slugify
from app.models import Circuit, Event
from app.services import circuit_event_score


def test_slugify_is_stable():
    assert slugify("Autódromo José Carlos Pace") == "aut-dromo-jos-carlos-pace"


def test_session_id_parser():
    assert FastF1Adapter.parse_session_id("2025-12-R") == (2025, 12, "R")


def test_artifact_keys_change_with_options():
    first = FastF1Adapter.artifact_key("2025-1-Q", "telemetry", {"drivers": "VER"})
    second = FastF1Adapter.artifact_key("2025-1-Q", "telemetry", {"drivers": "NOR"})
    assert first != second
    assert first.startswith("v3:")
    assert FastF1Adapter.bundle_key("2025-1-Q") == "v3:2025-1-Q:core-bundle"


def test_circuit_matching_handles_event_location_aliases():
    circuit = Circuit(slug="spa", name="Circuit de Spa-Francorchamps", country="Belgium", locality="Spa")
    event = Event(id="2026-10", season=2026, round_number=10, name="Belgian Grand Prix",
                  country="Belgium", location="Spa-Francorchamps")
    assert circuit_event_score(circuit, event) >= 80
