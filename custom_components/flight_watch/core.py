"""Pure flight logic: FR24/AeroDataBox parsing, event detection, messages. No Home Assistant imports."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from .const import EV_ARRIVED_GATE, EV_CANCELED, EV_DIVERTED, EV_LANDED, EV_LEFT_GATE, EV_TOOK_OFF

GATE_FALLBACK = 15 * 60  # call it 'at the gate' this long after landing if FR24 never says so
DELAY_STEP = 15  # minutes: late enough to alert, and how far the estimate must move to alert again
SCHEDULE_STEP = 5  # minutes a scheduled time must move to count as a new schedule


def _airport(a: dict | None) -> dict:
    a = a or {}
    tz = a.get("timezone") or {}
    return {
        "iata": (a.get("code") or {}).get("iata"),
        "city": ((a.get("position") or {}).get("region") or {}).get("city"),
        "tz": tz.get("name"),
        "offset": tz.get("offset"),
        "lat": (a.get("position") or {}).get("latitude"),
        "lon": (a.get("position") or {}).get("longitude"),
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
        "canceled": ((status.get("generic") or {}).get("status") or {}).get("text") == "canceled",
        "diverted": ((status.get("generic") or {}).get("status") or {}).get("diverted"),  # IATA code it went to
        **_times(f.get("time") or {}),
    }
    if leg["diverted"]:
        leg["diverted_to"] = _airport(ap.get("real")) if ap.get("real") else {"iata": leg["diverted"]}
    for k in ("origin", "destination"):
        info = (ap.get(k) or {}).get("info") or {}
        leg[k].update(terminal=info.get("terminal"), gate=info.get("gate"), baggage=info.get("baggage"))
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


TAXI_KT = 5


def leg_events(leg: dict, now: float) -> list[str]:
    """Events this leg has reached, in order (the caller sends only the ones not in leg['sent'])."""
    if leg.get("canceled") and not (leg.get("real_dep") or leg.get("real_arr")):
        return [EV_CANCELED]
    ev = []
    # FR24 often goes live at the gate (transponder on while boarding), so wait for taxi speed.
    if (leg.get("live") and (leg.get("speed") or 0) >= TAXI_KT) or leg.get("real_dep") or leg.get("real_arr"):
        ev.append(EV_LEFT_GATE)
    if leg.get("real_dep") or leg.get("real_arr"):
        ev.append(EV_TOOK_OFF)
    if leg.get("diverted"):  # FR24 gives no landing time; 'landed in <destination>' would be wrong
        return [*ev, EV_DIVERTED]
    if leg.get("real_arr"):
        ev.append(EV_LANDED)
        if not leg.get("live") or now - leg["real_arr"] > GATE_FALLBACK:
            ev.append(EV_ARRIVED_GATE)
    return ev


def dep_delay(leg: dict) -> int:
    """Minutes the estimated departure is past the schedule (0 without an estimate)."""
    est, sched = leg.get("est_dep"), leg.get("sched_dep")
    return round((est - sched) / 60) if est and sched else 0


def delay_change(leg: dict) -> int | None:
    """The new delay when it is worth an alert (before takeoff only), else None.

    Alerts once 15+ min late, again whenever the estimate moves 15+ min from the last alert,
    and once more if it comes back under 15 (back on schedule)."""
    if leg.get("real_dep") or leg.get("real_arr") or leg.get("canceled") or EV_LEFT_GATE in leg.get("sent", []):
        return None
    d, last = dep_delay(leg), leg.get("delay_sent", 0)
    return d if abs(d - last) >= DELAY_STEP and max(d, last) >= DELAY_STEP else None


def schedule_of(legs: list[dict]) -> list[list]:
    """What schedule_moved compares: [origin, sched_dep, sched_arr] per leg."""
    return [[x["origin"].get("iata"), x.get("sched_dep"), x.get("sched_arr")] for x in legs]


def schedule_moved(old: list, new: list) -> bool:
    """Same leg (origin), but a scheduled time moved SCHEDULE_STEP+ min."""
    return old[0] == new[0] and any(a and b and abs(b - a) >= SCHEDULE_STEP * 60 for a, b in zip(old[1:], new[1:]))


def leg_finished(leg: dict, now: float) -> bool:
    """At the gate, or long past its arrival with no word from FR24."""
    end = leg.get("real_arr") or leg.get("est_arr") or leg.get("sched_arr") or leg.get("sched_dep") or 0
    sent = leg.get("sent", [])
    if EV_DIVERTED in sent and not leg.get("live"):
        return True
    return EV_ARRIVED_GATE in sent or EV_CANCELED in sent or now > end + 12 * 3600


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
        return f", in {m} min"
    h = round(m / 30) / 2
    return f", in {int(h) if h == int(h) else h} hour{'s' if h != 1 else ''}"


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


def _codes(legs: list[dict]) -> str:
    """'MCO → LAX', chained like route() but with codes only."""
    r = route(legs)
    return " → ".join(x.rsplit("(", 1)[-1].rstrip(")") for x in r.split(" → ")) if r else ""


def _sub(number: str, legs: list[dict], who: str) -> str:
    """Second line: 'AA1496 · MCO → LAX' (the number leads the headline instead when there is no who)."""
    return " · ".join(x for x in (number if who else "", _codes(legs)) if x)


def _city(ap: dict) -> str:
    return ap.get("city") or ap.get("iata") or "?"


def _where(d: dict) -> str:
    """'Terminal B · Gate 159 · Baggage claim T4C1', whichever FR24 has."""
    return " · ".join(f"{label} {d[k]}" for label, k in (("Terminal", "terminal"), ("Gate", "gate"), ("Baggage claim", "baggage")) if d.get(k))


def mins(m: int) -> str:
    """'45 min', '1 h 20 min'."""
    h, m = divmod(abs(m), 60)
    return " ".join(x for x in (f"{h} h" if h else "", f"{m} min" if m or not h else "") if x)


def delay_message(number: str, leg: dict, who: str, now: float, home: tzinfo) -> str:
    name = f"{who}'s flight" if who else number
    d = dep_delay(leg)
    head = f"⏰ {name} is delayed {mins(d)}" if d >= DELAY_STEP else f"✅ {name} is back on schedule"
    o = leg["origin"]
    dep = leg.get("est_dep") or leg["sched_dep"]
    lines = [head, _sub(number, [leg], who), f"Departs {fmt_time(dep, o, home)}{countdown(dep, now)}"]
    if dep != leg["sched_dep"]:
        lines.append(f"Scheduled {fmt_time(leg['sched_dep'], o, home)}")
    return "\n".join(x for x in lines if x)


def schedule_message(number: str, leg: dict, old: list, who: str, now: float, home: tzinfo) -> str:
    name = f"{who}'s flight" if who else number
    lines = [f"🗓️ {name} has a new schedule", _sub(number, [leg], who)]
    for label, ts, was, ap in (("Departs", leg.get("sched_dep"), old[1], leg["origin"]),
                               ("Lands", leg.get("sched_arr"), old[2], leg["destination"])):
        if ts:
            moved = was and abs(ts - was) >= SCHEDULE_STEP * 60
            lines.append(f"{label} {fmt_time(ts, ap, home)}" + (f", was {_clock(datetime.fromtimestamp(was, tz_of(ap)))}" if moved else ""))
    return "\n".join(x for x in lines if x)


def leg_message(event: str, leg: dict, who: str, now: float, home: tzinfo) -> str:
    name = who or leg["number"]
    sub = _sub(leg["number"], [leg], who)
    o, d = leg["origin"], leg["destination"]
    if event == EV_CANCELED:
        name = f"{who}'s flight" if who else leg["number"]
        return f"❌ {name} is canceled\n{' · '.join(x for x in (sub, 'was ' + fmt_time(leg['sched_dep'], o, home)) if x)}"
    if event == EV_DIVERTED:
        name = f"{who}'s flight" if who else leg["number"]
        return f"↪️ {name} diverted to {place(leg.get('diverted_to') or {})}\n{sub}".rstrip("\n")
    if event == EV_LEFT_GATE:
        gate = f"gate {o['gate']}" if o.get("gate") else "the gate"
        head, at, ap = f"🚶 {name} left {gate} in {_city(o)}", now, o
    elif event == EV_TOOK_OFF:
        head, at, ap = f"🛫 {name} took off from {_city(o)}", leg.get("real_dep") or now, o
    elif event == EV_LANDED:
        head, at, ap = f"🛬 {name} landed in {_city(d)}", leg.get("real_arr") or now, d
    else:
        head, at, ap = f"✅ {name} is at the gate in {_city(d)}", now, d
    msg = f"{head}\n{' · '.join(x for x in (sub, fmt_time(at, ap, home)) if x)}"
    if event == EV_TOOK_OFF:
        eta = leg.get("est_arr") or leg.get("sched_arr")
        if eta:
            msg += f"\nLands {fmt_time(eta, d, home)}{countdown(eta, now)}"
    if event in (EV_LANDED, EV_ARRIVED_GATE) and _where(d):
        msg += f"\n{_where(d)}"
    return msg


def tracking_message(number: str, legs: list[dict], who: str, now: float, home: tzinfo) -> str:
    head = f"📋 Tracking {who}'s flight" if who else f"📋 Tracking {number}"
    sub = _sub(number, legs, who)
    if not legs or not _codes(legs):
        return f"{head}\n{sub}" if sub else head
    first = next((x for x in legs if not x.get("real_arr")), legs[0])
    if first.get("real_dep"):  # added while airborne
        line = f"Took off {fmt_time(first['real_dep'], first['origin'], home)}"
        eta = first.get("est_arr") or first.get("sched_arr")
        if eta:
            line += f"\nLands {fmt_time(eta, first['destination'], home)}{countdown(eta, now)}"
    else:
        dep = first.get("est_dep") or first["sched_dep"]
        line = f"Departs {fmt_time(dep, first['origin'], home)}{countdown(dep, now)}"
        if dep_delay(first) >= DELAY_STEP:
            line += f" (delayed {mins(dep_delay(first))})"
        if _where(first["origin"]):
            line += f"\n{_where(first['origin'])}"
    return f"{head}\n{sub}\n{line}"


def removed_message(number: str, legs: list[dict], who: str) -> str:
    head = f"🛑 Stopped tracking {who}'s flight" if who else f"🛑 Stopped tracking {number}"
    sub = _sub(number, legs, who)
    return f"{head}\n{sub}" if sub else head
