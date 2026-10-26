"""A map entity per airborne leg (source 'flight_watch'), for the map card."""

from __future__ import annotations

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.location import distance

from .const import DOMAIN, SIGNAL_UPDATE, STATE_TRACKING


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    fw = entry.runtime_data
    shown: dict[str, FlightPosition] = {}

    @callback
    def sync() -> None:
        live = {}
        for e in fw.entries.values():
            if e["state"] != STATE_TRACKING:
                continue
            for leg in e["legs"]:
                if leg.get("live") and leg.get("lat") is not None:
                    live[f"{e['id']}_{leg.get('fr24_id') or leg['sched_dep']}"] = (e, leg)
        for key in set(shown) - set(live):
            hass.async_create_task(shown.pop(key).async_remove(force_remove=True))
        new = []
        for key, (e, leg) in live.items():
            if key in shown:
                shown[key].update(e, leg)
            else:
                shown[key] = FlightPosition(key, e, leg)
                new.append(shown[key])
        if new:
            add(new)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_UPDATE, sync))
    sync()


class FlightPosition(GeolocationEvent):
    _attr_should_poll = False
    _attr_source = DOMAIN
    _attr_icon = "mdi:airplane"
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, key: str, e: dict, leg: dict) -> None:
        self._attr_unique_id = key
        self._set(e, leg)

    def _set(self, e: dict, leg: dict) -> None:
        self._attr_name = e.get("who") or leg["number"]
        self._attr_latitude = leg["lat"]
        self._attr_longitude = leg["lon"]
        # A changing state (km from origin) makes each position a "significant" history change, so the map's trail survives a page reload.
        o = leg.get("origin") or {}
        self._attr_distance = distance(o["lat"], o["lon"], leg["lat"], leg["lon"]) / 1000 if o.get("lat") is not None else None
        self._attr_extra_state_attributes = {
            "flight": leg["number"], "altitude": leg.get("alt"), "speed": leg.get("speed"), "heading": leg.get("heading"),
        }

    @callback
    def update(self, e: dict, leg: dict) -> None:
        self._set(e, leg)
        if self.hass:
            self.async_write_ha_state()
