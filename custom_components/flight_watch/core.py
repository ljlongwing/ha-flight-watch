"""Pure flight logic: FR24/AeroDataBox parsing, event detection, messages. No Home Assistant imports."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from .airlines import airline_name
from .const import EV_ARRIVED_GATE, EV_LANDED, EV_LEFT_GATE, EV_TOOK_OFF

GATE_FALLBACK = 15 * 60  # call it 'at the gate' this long after landing if FR24 never says so


def _airport(a: dict | None) -> dict:
    a = a or {}
    tz = a.get("timezone") or {}
    return {
        "iata": (a.get("code") or {}).get("iata"),
        "city": ((a.get("position") or {}).get("region") or {}).get("city"),
        "tz": tz.get("name"),
        "offset": tz.get("offset"),
    }


def _times(t: dict) -> dict:
    g = lambda k, s: ((t or {}).get(k) or {}).get(s)  # noqa: E731
    return {
        "sched_dep": g("scheduled", "departure"),
        "sched_arr": g("scheduled", "arrival"),
        "real_dep": g("real", "departure"),
        "real_arr": g("real", "arrival"),
        "est_dep": g("estimated", "departure"),
        "est_arr": g("estimated", "arrival") or g("other", "eta"),
    }


def leg_from_fr24(f: dict) -> dict:
    """One flight/list.json data row or a clickhandler answer -> a leg."""
    ident = f.get("identification") or {}
    status = f.get("status") or {}
    airline = f.get("airline") or {}
    ap = f.get("airport") or {}
    leg = {
        "number": ((ident.get("number") or {}).get("default") or "").upper(),
        "callsign": ident.get("callsign"),
        "fr24_id": ident.get("id"),
        "row": ident.get("row"),
        "airline": airline.get("short") or airline.get("name"),
        "origin": _airport(ap.get("origin")),
        "destination": _airport(ap.get("destination")),
        "live": bool(status.get("live")),
        "status": status.get("text"),
        **_times(f.get("time") or {}),
    }
    dest = ap.get("destination") or {}
    leg["destination"]["terminal"] = (dest.get("info") or {}).get("terminal")
    trail = f.get("trail") or []
    if trail:
        p = trail[0]  # newest first
        leg.update(lat=p.get("lat"), lon=p.get("lng"), alt=p.get("alt"), speed=p.get("spd"), heading=p.get("hd"))
    return leg


def legs_from_list(data: dict) -> list[dict]:
    rows = (((data or {}).get("result") or {}).get("response") or {}).get("data") or []
    return [leg_from_fr24(r) for r in rows if (r.get("time") or {}).get("scheduled", {}).get("departure")]


def list_range(legs: list[dict]) -> tuple[date, date] | None:
    """First and last origin-local departure date in a flight list."""
    days = [local_date(leg) for leg in legs]
    return (min(days), max(days)) if days else None


def tz_of(ap: dict) -> tzinfo:
    try:
        return ZoneInfo(ap["tz"]) if ap.get("tz") else timezone(timedelta(seconds=ap.get("offset") or 0))
    except (KeyError, ValueError):
        return timezone(timedelta(seconds=ap.get("offset") or 0))


def local_date(leg: dict) -> date:
    return datetime.fromtimestamp(leg["sched_dep"], tz_of(leg["origin"])).date()


def legs_on_date(legs: list[dict], day: date) -> list[dict]:
    """Legs whose origin-local scheduled departure falls on `day`, in departure order."""
    return sorted((x for x in legs if local_date(x) == day), key=lambda x: x["sched_dep"])


def pick_legs(legs: list[dict], board: str | None = None, leave: str | None = None,
              connect_from: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """The legs the passenger is on (one flight number can fly several legs a day).

    Boards at `board`, else where their earlier flight that day lands (`connect_from`), else the first leg.
    Gets off at `leave`, else before the route doubles back (AA590 DFW→MCO→DFW stops at MCO).
    """
    starts = {board} if board else connect_from
    i = next((k for k, x in enumerate(legs) if x["origin"].get("iata") in starts), 0)
    out: list[dict] = []
    seen = {legs[i]["origin"].get("iata")} if legs else set()
    for x in legs[i:]:
        dest = x["destination"].get("iata")
        if not leave and dest in seen:
            break
        out.append(x)
        seen.add(dest)
        if dest == leave:
            break
    return out


def same_leg(a: dict, b: dict) -> bool:
    if a.get("fr24_id") and a.get("fr24_id") == b.get("fr24_id"):
        return True
    if a.get("row") and a.get("row") == b.get("row"):
        return True
    return a.get("origin", {}).get("iata") == b.get("origin", {}).get("iata") and abs(
        (a.get("sched_dep") or 0) - (b.get("sched_dep") or 0)
    ) < 6 * 3600


def merge_leg(old: dict, new: dict) -> dict:
    """New data wins, but keep what the new answer lacks (sent events, position, ids)."""
    out = dict(old)
    for k, v in new.items():
        if v is not None and v != {} and k not in ("origin", "destination"):
            out[k] = v
    for side in ("origin", "destination"):
        merged = dict(old.get(side) or {})
        merged.update({k: v for k, v in (new.get(side) or {}).items() if v is not None})
        out[side] = merged
    out["live"] = new.get("live", old.get("live"))
    out.setdefault("sent", [])
    return out


def leg_events(leg: dict, now: float) -> list[str]:
    """Events this leg has reached, in order (the caller sends only the ones not in leg['sent'])."""
    ev = []
    if leg.get("live") or leg.get("real_dep") or leg.get("real_arr"):
        ev.append(EV_LEFT_GATE)
    if leg.get("real_dep") or leg.get("real_arr"):
        ev.append(EV_TOOK_OFF)
    if leg.get("real_arr"):
        ev.append(EV_LANDED)
        if not leg.get("live") or now - leg["real_arr"] > GATE_FALLBACK:
            ev.append(EV_ARRIVED_GATE)
    return ev


def leg_finished(leg: dict, now: float) -> bool:
    """At the gate, or long past its arrival with no word from FR24."""
    end = leg.get("real_arr") or leg.get("est_arr") or leg.get("sched_arr") or leg.get("sched_dep") or 0
    return EV_ARRIVED_GATE in leg.get("sent", []) or now > end + 12 * 3600


def leg_from_adb(a: dict) -> dict | None:
    """One AeroDataBox /flights/number row -> a schedule-only leg."""
    dep, arr = a.get("departure") or {}, a.get("arrival") or {}
    try:
        sd = datetime.fromisoformat(dep["scheduledTime"]["utc"].replace("Z", "+00:00"))
        dl = datetime.fromisoformat(dep["scheduledTime"]["local"])
    except (KeyError, TypeError, ValueError):
        return None
    sa = None
    try:
        sa = datetime.fromisoformat(arr["scheduledTime"]["utc"].replace("Z", "+00:00")).timestamp()
        al = datetime.fromisoformat(arr["scheduledTime"]["local"])
    except (KeyError, TypeError, ValueError):
        al = None

    def ap(x, loc):
        x = x.get("airport") or {}
        off = loc.utcoffset().total_seconds() if loc and loc.utcoffset() is not None else None
        return {"iata": x.get("iata"), "city": x.get("municipalityName"), "tz": x.get("timeZone"), "offset": off}

    return {
        "number": (a.get("number") or "").replace(" ", "").upper(),
        "airline": (a.get("airline") or {}).get("name"),
        "origin": ap(dep, dl),
        "destination": ap(arr, al),
        "sched_dep": sd.timestamp(),
        "sched_arr": sa,
        "live": False,
        "source": "adb",
    }


# ---------- messages ----------


def _clock(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def fmt_time(ts: float, ap: dict, home: tzinfo) -> str:
    """'2:00 PM EDT', plus the home time in () when the airport's zone differs."""
    loc = datetime.fromtimestamp(ts, tz_of(ap))
    out = f"{_clock(loc)} {loc.tzname() or ''}".strip()
    h = datetime.fromtimestamp(ts, home)
    if h.utcoffset() != loc.utcoffset():
        out += f" ({_clock(h)} {h.tzname()})"
    return out


def countdown(ts: float, now: float) -> str:
    m = round((ts - now) / 60)
    if m <= 0:
        return ""
    if m < 60:
        return f" · in {m} min"
    h = round(m / 30) / 2
    return f" · in {int(h) if h == int(h) else h} hour{'s' if h != 1 else ''}"


def place(ap: dict) -> str:
    city, code = ap.get("city"), ap.get("iata")
    return f"{city} ({code})" if city and code else (city or code or "?")


def route(legs: list[dict]) -> str:
    """'Denver (DEN) → Nashville (BNA) → Orlando (MCO)', chained, stopping before an airport repeats."""
    if not legs or not legs[0].get("origin", {}).get("iata"):
        return ""
    stops = [legs[0]["origin"]]
    for leg in legs:
        if leg["destination"].get("iata") in [s.get("iata") for s in stops]:
            break
        stops.append(leg["destination"])
    return " → ".join(place(s) for s in stops)


def full_number(number: str, airline: str | None) -> str:
    al = airline or airline_name(number)
    return f"{al} {number}" if al else number


def leg_message(event: str, leg: dict, who: str, now: float, home: tzinfo) -> str:
    num = full_number(leg["number"], leg.get("airline"))
    name = f"{who}'s flight" if who else num
    r = route([leg])
    detail = f" ({num}, {r})" if who and r else (f" ({r})" if r else (f" ({num})" if who else ""))
    o, d = leg["origin"], leg["destination"]
    if event == EV_LEFT_GATE:
        return f"🚶 {name}{detail} left the gate at {fmt_time(now, o, home)}"
    if event == EV_TOOK_OFF:
        eta = leg.get("est_arr") or leg.get("sched_arr")
        eta_s = f"{fmt_time(eta, d, home)}{countdown(eta, now)}" if eta else "unknown"
        return f"🛫 {name}{detail} took off at {fmt_time(leg.get('real_dep') or now, o, home)}, ETA {eta_s}"
    if event == EV_LANDED:
        return f"🛬 {name}{detail} landed at {fmt_time(leg.get('real_arr') or now, d, home)}"
    term = f", terminal {d['terminal']}" if d.get("terminal") else ""
    return f"✅ {name}{detail} at the gate at {fmt_time(now, d, home)}{term}"


def tracking_message(number: str, legs: list[dict], who: str, now: float, home: tzinfo) -> str:
    num = full_number(number, legs[0].get("airline") if legs else None)
    name = f"{who}'s flight {num}" if who else num
    r = route(legs)
    if not r:
        return f"📋 Now tracking {name}"
    first = next((x for x in legs if not x.get("real_arr")), legs[0])
    if first.get("real_dep"):  # added while airborne
        eta = first.get("est_arr") or first.get("sched_arr")
        eta_s = f", ETA {fmt_time(eta, first['destination'], home)}{countdown(eta, now)}" if eta else ""
        return f"📋 Now tracking {name} ({r}), took off at {fmt_time(first['real_dep'], first['origin'], home)}{eta_s}"
    dep = first.get("est_dep") or first["sched_dep"]
    return f"📋 Now tracking {name} ({r}), departs {fmt_time(dep, first['origin'], home)}{countdown(dep, now)}"


def removed_message(number: str, legs: list[dict], who: str) -> str:
    num = full_number(number, legs[0].get("airline") if legs else None)
    r = route(legs)
    return f"🛑 Stopped tracking {(who + chr(39) + 's flight ' + num) if who else num}{f' ({r})' if r else ''}. No more alerts for it."
