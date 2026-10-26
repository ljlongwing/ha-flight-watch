"""One sensor: number of flights being tracked; attributes feed the card."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALT_UNITS, CONF_ALT_UNIT, CONF_SPEED_UNIT, DOMAIN, SIGNAL_UPDATE, SPEED_UNITS, STATE_DONE, STATE_TRACKING, STATE_UPCOMING


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([FlightsSensor(entry)])


class FlightsSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "flights"
    _attr_icon = "mdi:airplane-clock"
    _attr_should_poll = False
    # Big and changes every minute; keep it out of the recorder database.
    _unrecorded_attributes = frozenset({STATE_TRACKING, STATE_UPCOMING, STATE_DONE})

    def __init__(self, entry: ConfigEntry) -> None:
        self._fw = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_flights"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Flight Watch", entry_type=DeviceEntryType.SERVICE
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self.async_write_ha_state))

    @property
    def native_value(self) -> int:
        return sum(e["state"] == STATE_TRACKING for e in self._fw.entries.values())

    @property
    def extra_state_attributes(self) -> dict:
        o = self._fw.options
        return {**self._fw.snapshot(), "units": {"alt": o.get(CONF_ALT_UNIT, ALT_UNITS[0]), "speed": o.get(CONF_SPEED_UNIT, SPEED_UNITS[0])}}
