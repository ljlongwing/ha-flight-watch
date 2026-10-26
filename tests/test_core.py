import json
import sys
import types
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# Import the pure modules without running the package __init__ (which needs Home Assistant).
_pkg = types.ModuleType("flight_watch")
_pkg.__path__ = [str(Path(__file__).parents[1] / "custom_components" / "flight_watch")]
sys.modules["flight_watch"] = _pkg
from flight_watch import core  # noqa: E402
from flight_watch.airlines import parse_flights  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
DENVER = ZoneInfo("America/Denver")


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf8"))


@pytest.mark.parametrize(
    "text, want",
    [
        ("Alaska 300", ["AS300"]),
        ("alaska airlines 300", ["AS300"]),
        ("AS 300", ["AS300"]),
        ("ASA300", ["AS300"]),
        ("Southwest 1570/1875", ["WN1570", "WN1875"]),
        ("AS300, AA590", ["AS300", "AA590"]),
        ("AA 2104 + 590", ["AA2104", "AA590"]),
        ("B6 123", ["B6123"]),
        ("zz999", ["ZZ999"]),
    ],
)
def test_parse_flights(text, want):
    assert parse_flights(text) == want


@pytest.mark.parametrize("text", ["300", "Nowhere Air 12", "", "AS"])
def test_parse_flights_bad(text):
    with pytest.raises(ValueError):
        parse_flights(text)


def test_list_picks_by_origin_local_date():
    legs = core.legs_from_list(load("aa1.json"))
    assert len(legs) == 16
    today = core.legs_on_date(legs, date(2026, 10, 24))
    assert [x["fr24_id"] for x in today] == ["424511e4"]
    assert today[0]["origin"]["iata"] == "JFK" and today[0]["live"]
    # Nov 1 is after the DST change: still the right date in New York
    assert len(core.legs_on_date(legs, date(2026, 11, 1))) == 1
    assert core.list_range(legs) == (date(2026, 10, 17), date(2026, 11, 1))


def test_clickhandler_leg_and_events():
    leg = core.leg_from_fr24(load("ch.json"))
    assert leg["fr24_id"] == "424511e4" and leg["airline"] == "American Airlines"
    assert leg["lat"] == 38.566132 and leg["alt"] == 36000
    now = 1792852000
    assert core.leg_events(leg, now) == ["left_gate", "took_off"]
    landed = {**leg, "real_arr": now, "live": True}
    assert core.leg_events(landed, now + 60)[-1] == "landed"
    assert core.leg_events(landed, now + 16 * 60)[-1] == "arrived_gate"  # fallback
    assert core.leg_events({**landed, "live": False}, now + 60)[-1] == "arrived_gate"


def test_landed_flight_from_list_is_all_events():
    legs = core.legs_from_list(load("aa1.json"))
    old = core.legs_on_date(legs, date(2026, 10, 23))[0]
    assert core.leg_events(old, 1792800000) == ["left_gate", "took_off", "landed", "arrived_gate"]


def test_merge_keeps_sent_and_position():
    old = {"number": "AA1", "sent": ["left_gate"], "lat": 1.0, "origin": {"iata": "JFK", "city": "New York"},
           "destination": {"iata": "LAX"}, "live": True}
    new = {"number": "AA1", "lat": None, "real_dep": 5, "origin": {"iata": "JFK", "city": None},
           "destination": {"iata": "LAX", "terminal": "4"}, "live": False}
    m = core.merge_leg(old, new)
    assert m["sent"] == ["left_gate"] and m["lat"] == 1.0 and m["real_dep"] == 5
    assert m["origin"]["city"] == "New York" and m["destination"]["terminal"] == "4" and m["live"] is False


def test_messages():
    leg = core.leg_from_fr24(load("ch.json"))
    leg["origin"]["city"], leg["destination"]["city"] = "New York", "Los Angeles"
    now = leg["real_dep"]
    msg = core.leg_message("took_off", leg, "Tony", now, DENVER)
    assert msg.startswith("🛫 Tony's flight (American Airlines AA1, New York (JFK) → Los Angeles (LAX)) took off at ")
    assert "(" in msg.split("took off at ")[1] and "ETA" in msg and "· in 5" in msg
    assert core.leg_message("landed", leg, "", now, DENVER).startswith(
        "🛬 American Airlines AA1 (New York (JFK) → Los Angeles (LAX)) landed at")
    legs = [leg]
    assert core.removed_message("AA1", legs, "") == (
        "🛑 Stopped tracking American Airlines AA1 (New York (JFK) → Los Angeles (LAX)). No more alerts for it.")
    assert core.tracking_message("WN1875", [], "", now, DENVER) == "📋 Now tracking Southwest WN1875"


def test_fmt_time_same_zone_has_no_parens():
    ny = {"tz": "America/New_York"}
    assert core.fmt_time(1792846114, ny, ZoneInfo("America/New_York")) == "8:48 AM EDT"
    assert core.fmt_time(1792846114, ny, DENVER) == "8:48 AM EDT (6:48 AM MDT)"


def test_countdown():
    assert core.countdown(100 + 45 * 60, 100) == " · in 45 min"
    assert core.countdown(100 + 90 * 60, 100) == " · in 1.5 hours"
    assert core.countdown(100 + 60 * 60, 100) == " · in 1 hour"
    assert core.countdown(50, 100) == ""


def test_route_stops_before_repeat():
    a = lambda c: {"iata": c, "city": None}  # noqa: E731
    legs = [{"origin": a("DEN"), "destination": a("BNA")}, {"origin": a("BNA"), "destination": a("MCO")},
            {"origin": a("MCO"), "destination": a("DEN")}]
    assert core.route(legs) == "DEN → BNA → MCO"


def _legs(*codes):
    a = lambda c: {"iata": c, "city": None}  # noqa: E731
    return [{"origin": a(o), "destination": a(d)} for o, d in zip(codes, codes[1:])]


def _hops(legs):
    return [(x["origin"]["iata"], x["destination"]["iata"]) for x in legs]


def test_pick_legs():
    aa590 = _legs("DFW", "MCO", "DFW")  # continues back after Heather gets off
    assert _hops(core.pick_legs(aa590)) == [("DFW", "MCO")]
    assert _hops(core.pick_legs(aa590, board="MCO")) == [("MCO", "DFW")]
    wn = _legs("DEN", "BNA", "MCO", "FLL")
    assert len(core.pick_legs(wn)) == 3
    assert _hops(core.pick_legs(wn, leave="BNA")) == [("DEN", "BNA")]
    assert _hops(core.pick_legs(wn, board="BNA", leave="MCO")) == [("BNA", "MCO")]
    assert _hops(core.pick_legs(wn, connect_from={"BNA"})) == [("BNA", "MCO"), ("MCO", "FLL")]
    assert _hops(core.pick_legs(wn, board="XXX")) == _hops(wn)  # unknown airport: no trim
    assert core.pick_legs([]) == []


def test_adb_leg():
    row = {"number": "WN 1570", "airline": {"name": "Southwest"},
           "departure": {"airport": {"iata": "DEN", "municipalityName": "Denver", "timeZone": "America/Denver"},
                         "scheduledTime": {"utc": "2027-01-05 13:00Z", "local": "2027-01-05 06:00-07:00"}},
           "arrival": {"airport": {"iata": "BNA", "municipalityName": "Nashville"},
                       "scheduledTime": {"utc": "2027-01-05 16:00Z", "local": "2027-01-05 10:00-06:00"}}}
    leg = core.leg_from_adb(row)
    assert leg["number"] == "WN1570" and core.local_date(leg) == date(2027, 1, 5)
    assert leg["destination"]["offset"] == -6 * 3600


def test_tracking_message_when_airborne():
    leg = core.leg_from_fr24(load("ch.json"))
    msg = core.tracking_message("AA1", [leg], "", leg["real_dep"] + 60, DENVER)
    assert ", took off at 8:48 AM EDT (6:48 AM MDT), ETA " in msg
