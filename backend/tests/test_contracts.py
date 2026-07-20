from app.contracts import driver_role, is_reserve_driver


def test_explicit_reserve_role_wins_over_a_complete_profile():
    row = {
        "driverRole": "Reserve Driver",
        "driverNumber": "99",
        "driverCode": "RSV",
        "driverUrl": "https://example.test/reserve",
    }

    assert is_reserve_driver(row) is True
    assert driver_role(row) == "reserve"


def test_explicit_race_role_wins_over_missing_profile_metadata():
    row = {
        "role": "race-driver",
        "givenName": "Main",
        "familyName": "Driver",
    }

    assert is_reserve_driver(row) is False
    assert driver_role(row) == "race"


def test_name_only_driver_defaults_to_reserve():
    row = {"givenName": "Reserve", "familyName": "Driver"}

    assert is_reserve_driver(row) is True
    assert driver_role(row) == "reserve"
