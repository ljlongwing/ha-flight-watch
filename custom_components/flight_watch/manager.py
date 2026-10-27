"""Flight lifecycle: upcoming -> tracking -> done, with polling, events and notifications."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from datetime import date, timedelta

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import core
from .api import Api, RateLimited
from .const import (
    ALL_EVENTS,
    CONF_ADB_KEY,
    CONF_EVENTS,
    CONF_NOTIFY,
    DOMAIN,
    EV_DELAYED,
    EV_PROBLEM,
    EV_REMOVED,
    EV_TRACKING,
    EVENT,
    FR24_WINDOW_DAYS,
    SIGNAL_UPDATE,
    STATE_DONE,
    STATE_TRACKING,
    STATE_UPCOMING,
)

_LOGGER = logging.getLogger(__name__)
MIN, HOUR, DAY = 60, 3600, 86400


class FlightWatch:
    def __init__(self, hass: HomeAssistant, options: dict) -> None:
        self.hass = hass
        self.options = options
        self.api = Api(async_get_clientsession(hass))
        self._store = Store(hass, 1, DOMAIN)
        self.entries: dict[str, dict] = {}
        self.colors: dict[str, str] = {}  # who -> map color, picked on the card
        self._lock = asyncio.Lock()
        self._unsub = None
        self._again = False

    # ---------- lifecycle ----------

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.entries = {e["id"]: e for e in data.get("entries", [])}
        self.colors = data.get("colors", {})
        self._unsub = async_track_time_interval(self.hass, self._tick, timedelta(seconds=MIN))
        self.hass.async_create_task(self.async_tick())

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
        await self._save()

    async def _save(self) -> None:
        await self._store.async_save({"entries": list(self.entries.values()), "colors": self.colors})

    @callback
    def _tick(self, _now) -> None:
        self.hass.async_create_task(self.async_tick())

    async def async_tick(self) -> None:
        if self._lock.locked():
            self._again = True  # e.g. a flight added mid-tick: run once more right after
            return
        self._again = False
        async with self._lock:
            now = time.time()
            today = dt_util.now().date()
            for e in list(self.entries.values()):
                if e.get("next_check", 0) > now:
                    continue
                try:
                    if e["state"] == STATE_UPCOMING:
                        await self._upcoming(e, now, today)
                    elif e["state"] == STATE_TRACKING:
                        await self._tracking(e, now)
                    elif now > e.get("done_at", now) + 12 * HOUR:
                        self.entries.pop(e["id"], None)
                except RateLimited:
                    e["next_check"] = now + 10 * MIN
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                    _LOGGER.debug("%s: %s", e["number"], err)
                    e["next_check"] = now + 5 * MIN
            await self._save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        if self._again:
            self.hass.async_create_task(self.async_tick())

    # ---------- upcoming ----------

    async def _upcoming(self, e: dict, now: float, today: date) -> None:
        day = date.fromisoformat(e["date"])
        if day < today - timedelta(days=1):
            self.entries.pop(e["id"], None)  # never found and long past
            return
        if day <= today:
            await self._activate(e, now, day)
            return
        e["next_check"] = now + 6 * HOUR
        if day <= today + timedelta(days=FR24_WINDOW_DAYS):
            legs = self._pick(e, core.legs_on_date(core.legs_from_list(await self.api.flight_list(e["number"])), day))
            if legs:
                e["legs"], e["source"] = legs, "fr24"
        elif self.options.get(CONF_ADB_KEY) and e.get("source") != "fr24" and now - e.get("adb_checked", 0) > 7 * DAY:
            e["adb_checked"] = now
            rows = await self.api.adb(self.options[CONF_ADB_KEY], e["number"], e["date"]) or []
            legs = self._pick(e, sorted(filter(None, map(core.leg_from_adb, rows)), key=lambda x: x["sched_dep"]))
            if legs:
                e["legs"], e["source"] = legs, "adb"

    async def _activate(self, e: dict, now: float, day: date) -> None:
        all_legs = core.legs_from_list(await self.api.flight_list(e["number"]))
        legs = self._pick(e, core.legs_on_date(all_legs, day))
        if not legs:
            e["next_check"] = now + HOUR
            if not e.get("problem_sent"):
                e["problem_sent"] = True
                rng = core.list_range(all_legs)
                why = f"FR24 has no {e['number']} on {day:%a %b} {day.day}" if rng and rng[0] <= day <= rng[1] else (
                    f"FR24 doesn't list {e['number']} for {day:%a %b} {day.day} yet")
                await self._notify(EV_PROBLEM, e, f"⚠️ {why}; will keep checking hourly.")
            return
        if all(x.get("real_arr") for x in legs):
            self.entries.pop(e["id"], None)
            await self._notify(EV_PROBLEM, e, f"⚠️ Not tracking {e['number']}: its {day:%a %b} {day.day} flight has already landed")
            return
        for leg in legs:  # what already happened isn't news (the tracking message mentions a delay)
            leg["sent"] = core.leg_events(leg, now)
            leg["delay_sent"] = core.dep_delay(leg)
        e.update(legs=legs, source="fr24", state=STATE_TRACKING, next_check=now)
        await self._notify(EV_TRACKING, e, core.tracking_message(e["number"], legs, e.get("who", ""), now, self._home))

    # ---------- tracking ----------

    async def _tracking(self, e: dict, now: float) -> None:
        legs = e["legs"]
        active = [x for x in legs if not core.leg_finished(x, now)]
        if not active:
            e.update(state=STATE_DONE, done_at=now)
            return
        leg = active[0]
        dep = leg.get("est_dep") or leg["sched_dep"]
        near = leg.get("live") or leg.get("real_dep") or now > dep - 3 * HOUR
        if near and leg.get("fr24_id"):
            fresh = [core.leg_from_fr24(await self.api.live(leg["fr24_id"]))]
        else:
            fresh = core.legs_from_list(await self.api.flight_list(e["number"]))
        for i, old in enumerate(legs):
            new = next((n for n in fresh if core.same_leg(old, n)), None)
            if new:
                legs[i] = core.merge_leg(old, new)
        for x in legs:
            d = core.delay_change(x)
            if d is not None:
                x["delay_sent"] = d
                await self._notify(EV_DELAYED, e, core.delay_message(x["number"], x, e.get("who", ""), now, self._home), x)
            for ev in core.leg_events(x, now):
                if ev not in x["sent"]:
                    x["sent"].append(ev)
                    await self._notify(ev, e, core.leg_message(ev, x, e.get("who", ""), now, self._home), x)
        leg = next((x for x in legs if not core.leg_finished(x, now)), None)
        if leg is None:
            e.update(state=STATE_DONE, done_at=now, next_check=now)
        elif leg.get("live") or leg.get("real_dep") or now > (leg.get("est_dep") or leg["sched_dep"]) - 3 * HOUR:
            e["next_check"] = now + (MIN if leg.get("fr24_id") else 2 * MIN)
        else:
            e["next_check"] = now + 15 * MIN

    # ---------- services ----------

    def _pick(self, e: dict, legs: list[dict]) -> list[dict]:
        """Only the legs this person flies; a connection starts where their other flight that day lands."""
        lands = {o["legs"][-1]["destination"].get("iata") for o in self.entries.values()
                 if o is not e and e.get("who") and o.get("who") == e["who"] and o["date"] == e["date"] and o["legs"]}
        return core.pick_legs(legs, e.get("board"), e.get("leave"), lands)

    async def async_lookup(self, numbers: list[str], day: date) -> list[dict]:
        """Every leg each flight flies that day, so the card can ask where the person gets on and off."""
        today = dt_util.now().date()
        out = []
        for n in numbers:
            legs: list[dict] = []
            try:
                if today - timedelta(days=1) <= day <= today + timedelta(days=FR24_WINDOW_DAYS):
                    legs = core.legs_on_date(core.legs_from_list(await self.api.flight_list(n)), day)
                elif self.options.get(CONF_ADB_KEY):
                    rows = await self.api.adb(self.options[CONF_ADB_KEY], n, day.isoformat()) or []
                    legs = sorted(filter(None, map(core.leg_from_adb, rows)), key=lambda x: x["sched_dep"])
            except (RateLimited, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                _LOGGER.debug("lookup %s: %s", n, err)  # legs unknown: added anyway, picked automatically later
            out.append({"number": n, "legs": [{k: x.get(k) for k in ("origin", "destination", "sched_dep", "sched_arr")}
                                               for x in legs]})
        return out

    async def async_add(self, numbers: list[str], day: date, who: str,
                        board: str | None = None, leave: str | None = None) -> None:
        for i, n in enumerate(numbers):
            eid = uuid.uuid4().hex[:12]
            self.entries[eid] = {"id": eid, "number": n, "date": day.isoformat(), "who": who.strip(),
                                 "state": STATE_UPCOMING, "legs": [], "added": time.time(), "next_check": 0,
                                 # several numbers = one trip: boards the first, gets off the last
                                 "board": board if i == 0 else None, "leave": leave if i == len(numbers) - 1 else None}
        await self._save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        self.hass.async_create_task(self.async_tick())

    async def async_remove(self, ids: list[str]) -> None:
        for eid in ids:
            e = self.entries.pop(eid, None)
            if e and e["state"] == STATE_TRACKING:
                await self._notify(EV_REMOVED, e, core.removed_message(e["number"], e["legs"], e.get("who", "")))
        await self._save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def async_set_color(self, who: str, color: str) -> None:
        if color:
            self.colors[who] = color
        else:
            self.colors.pop(who, None)
        await self._save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # ---------- output ----------

    @property
    def _home(self):
        return dt_util.get_default_time_zone()

    async def _notify(self, event: str, e: dict, message: str, leg: dict | None = None) -> None:
        self.hass.bus.async_fire(EVENT, {
            "type": event, "id": e["id"], "flight": e["number"], "date": e["date"], "who": e.get("who", ""),
            "message": message, "leg": {k: v for k, v in (leg or {}).items() if k != "sent"},
        })
        if event not in self.options.get(CONF_EVENTS, ALL_EVENTS):
            return
        targets = self.options.get(CONF_NOTIFY) or []
        if targets:
            await self.hass.services.async_call("notify", "send_message", {"entity_id": targets, "message": message})
        else:
            await self.hass.services.async_call(
                "persistent_notification", "create", {"title": "Flight Watch", "message": message})

    def snapshot(self) -> dict:
        """What the sensor exposes for the card (a deep copy: HA only pushes state when it differs from the last one)."""
        out = {STATE_UPCOMING: [], STATE_TRACKING: [], STATE_DONE: []}
        for e in self.entries.values():
            out[e["state"]].append(copy.deepcopy({k: e.get(k) for k in ("id", "number", "date", "who", "legs", "source")}))
        for v in out.values():
            v.sort(key=lambda x: (x["date"], (x["legs"] or [{}])[0].get("sched_dep") or 1e12))
        out["colors"] = dict(self.colors)
        return out
