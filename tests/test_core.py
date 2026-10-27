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
    head, sub, lands = msg.split("\n")
    assert head == "🛫 Tony took off from New York"
    assert sub == "AA1 · JFK → LAX · 8:48 AM EDT (6:48 AM MDT)"
    assert lands.startswith("Lands ") and ", in 5" in lands
    assert core.leg_message("landed", leg, "", now, DENVER).startswith("🛬 AA1 landed in Los Angeles\nJFK → LAX · ")
    assert core.leg_message("left_gate", leg, "Tony", now, DENVER).startswith("🚶 Tony left gate 2 in New York\n")
    assert core.leg_message("arrived_gate", leg, "", now, DENVER).endswith("\nTerminal 4 · Gate 41 · Baggage claim T4C2")
    assert core.removed_message("AA1", [leg], "") == "🛑 Stopped tracking AA1\nJFK → LAX"
    early = {**leg, "real_dep": None}
    assert core.tracking_message("AA1", [early], "", now, DENVER).endswith("\nTerminal 8 · Gate 2")
    assert core.tracking_message("WN1875", [], "", now, DENVER) == "📋 Tracking WN1875"
    assert core.tracking_message("WN1875", [], "Tony", now, DENVER) == "📋 Tracking Tony's flight\nWN1875"


def test_left_gate_waits_for_taxi():
    leg = {"live": True, "speed": 0}
    assert core.leg_events(leg, 0) == []
    assert core.leg_events({**leg, "speed": 12}, 0) == ["left_gate"]


def test_fmt_time_same_zone_has_no_parens():
    ny = {"tz": "America/New_York"}
    assert core.fmt_time(1792846114, ny, ZoneInfo("America/New_York")) == "8:48 AM EDT"
    assert core.fmt_time(1792846114, ny, DENVER) == "8:48 AM EDT (6:48 AM MDT)"


def test_countdown():
    assert core.countdown(100 + 45 * 60, 100) == ", in 45 min"
    assert core.countdown(100 + 90 * 60, 100) == ", in 1.5 hours"
    assert core.countdown(100 + 60 * 60, 100) == ", in 1 hour"
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
    assert "\nTook off 8:48 AM EDT (6:48 AM MDT)\nLands " in msg


def test_delay_alerts():
    s = 1792935900  # AA1 Oct 25, 'Estimated dep 10:17' in the list = 32 min late
    leg = core.legs_on_date(core.legs_from_list(load("aa1.json")), date(2026, 10, 25))[0]
    assert core.dep_delay(leg) == 32 and core.delay_change(leg) == 32
    step = lambda d, last: core.delay_change({**leg, "est_dep": s + d * 60, "delay_sent": last})  # noqa: E731
    assert step(10, 0) is None  # under 15: not news
    assert step(40, 32) is None  # moved < 15
    assert step(50, 32) == 50  # worse by 18
    assert step(5, 32) == 5  # back on schedule
    assert step(5, 0) is None
    assert core.delay_change({**leg, "real_dep": s + 1}) is None  # in the air: landing ETA covers it
    assert core.delay_change({**leg, "sent": ["left_gate"]}) is None  # taxiing
    leg["origin"]["city"] = "New York"
    msg = core.delay_message("AA1", leg, "Tony", s - 3600, DENVER)
    assert msg.split("\n")[0] == "⏰ Tony's flight is delayed 32 min"
    assert msg.split("\n")[2].startswith("Departs 10:17 AM EDT (8:17 AM MDT), in 1.5 hours")
    assert msg.endswith("\nScheduled 9:45 AM EDT (7:45 AM MDT)")
    ok = core.delay_message("AA1", {**leg, "est_dep": s}, "", s - 3600, DENVER)
    assert ok.startswith("✅ AA1 is back on schedule\nJFK → LAX\nDeparts 9:45 AM EDT") and "Scheduled" not in ok
    assert "(delayed 32 min)" in core.tracking_message("AA1", [leg], "", s - 3600, DENVER)
    assert core.mins(80) == "1 h 20 min" and core.mins(60) == "1 h" and core.mins(0) == "0 min"


def test_canceled():
    # status block as FR24 sent it for AA9933 on 2026-10-27
    row = load("aa1.json")["result"]["response"]["data"][0]
    row = {**row, "status": {"live": False, "text": "Canceled", "generic": {"status": {"text": "canceled", "type": "departure"}}}}
    leg = core.leg_from_fr24(row)
    assert leg["canceled"] and core.leg_events(leg, 0) == ["canceled"]
    assert core.delay_change({**leg, "est_dep": leg["sched_dep"] + 3600}) is None
    assert core.leg_finished({**leg, "sent": ["canceled"]}, 0)
    assert not core.leg_from_fr24(load("ch.json"))["canceled"]
    leg["origin"]["city"] = "New York"
    msg = core.leg_message("canceled", leg, "Tony", 0, DENVER)
    assert msg.startswith("❌ Tony's flight is canceled\nAA1 · JFK → LAX · was ") and "\n" not in msg.split("\n", 1)[1]
