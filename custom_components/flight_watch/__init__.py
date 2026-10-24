"""Flight Watch: track specific flights on specific days, with alerts and a dashboard card."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .airlines import parse_flights
from .const import DOMAIN
from .manager import FlightWatch

PLATFORMS = ["sensor", "geo_location"]
CARD_URL = f"/{DOMAIN}/flight-watch-card.js"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ADD_SCHEMA = vol.Schema({
    vol.Required("flight"): cv.string,
    vol.Required("date"): cv.date,
    vol.Optional("who", default=""): cv.string,
})
REMOVE_SCHEMA = vol.Schema({vol.Required("id"): vol.All(cv.ensure_list, [cv.string])})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    version = (await hass.async_add_executor_job(_manifest_version))
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(Path(__file__).parent / "frontend" / "flight-watch-card.js"), False)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")

    def fw() -> FlightWatch:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("Flight Watch is not set up")
        return entries[0].runtime_data

    async def add(call: ServiceCall) -> None:
        try:
            numbers = parse_flights(call.data["flight"])
        except ValueError as err:
            raise ServiceValidationError(
                f"Don't know the airline in '{err}'. Use the 2-letter code, e.g. AS300 or 'Alaska 300'."
            ) from err
        await fw().async_add(numbers, call.data["date"], call.data["who"])

    async def remove(call: ServiceCall) -> None:
        await fw().async_remove(call.data["id"])

    hass.services.async_register(DOMAIN, "add_flight", add, ADD_SCHEMA)
    hass.services.async_register(DOMAIN, "remove_flight", remove, REMOVE_SCHEMA)
    return True


def _manifest_version() -> str:
    import json

    return json.loads((Path(__file__).parent / "manifest.json").read_text())["version"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.runtime_data = FlightWatch(hass, {**entry.data, **entry.options})
    await entry.runtime_data.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return ok
