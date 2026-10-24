"""Set up and change Flight Watch: where alerts go, which ones, optional AeroDataBox key."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import ALL_EVENTS, CONF_ADB_KEY, CONF_EVENTS, CONF_NOTIFY, DOMAIN


def _schema(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_NOTIFY, default=d.get(CONF_NOTIFY, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="notify", multiple=True)
        ),
        vol.Optional(CONF_EVENTS, default=d.get(CONF_EVENTS, ALL_EVENTS)): selector.SelectSelector(
            selector.SelectSelectorConfig(options=ALL_EVENTS, multiple=True, translation_key=CONF_EVENTS)
        ),
        vol.Optional(CONF_ADB_KEY, description={"suggested_value": d.get(CONF_ADB_KEY)}): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    })


class FlightWatchConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Flight Watch", data={}, options=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return FlightWatchOptionsFlow()


class FlightWatchOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=_schema(dict(self.config_entry.options)))
