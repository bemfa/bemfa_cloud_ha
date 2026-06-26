"""Config flow for Bemfa Cloud."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    AUTH_MODE_KEYS,
    AUTH_MODE_OAUTH,
    CONF_AUTH_MODE,
    CONF_REGION,
    CONF_UID,
    BEMFA_REGION,
    DOMAIN,
    LOGGER,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_TOKEN_URL,
    OPTIONS_CONFIG,
    OPTIONS_NAME,
    OPTIONS_SELECT,
)
from .sync import Sync

ERROR_CANNOT_SYNC = "cannot_sync"

STEP_KEYS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_UID): str,
    }
)

_UID_RE = re.compile(r"^[0-9a-fA-F]{32}$|^[A-Za-z0-9_-]{45}$")


class ConfigFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle a config flow for Bemfa Cloud."""

    VERSION = 1
    DOMAIN = DOMAIN

    @staticmethod
    def async_get_implementations(
        hass: HomeAssistant,
    ) -> list[config_entry_oauth2_flow.AbstractOAuth2Implementation]:
        """Return OAuth2 implementations."""

        return [
            config_entry_oauth2_flow.LocalOAuth2Implementation(
                hass,
                DOMAIN,
                OAUTH_CLIENT_ID,
                OAUTH_CLIENT_SECRET,
                OAUTH_AUTHORIZE_URL,
                OAUTH_TOKEN_URL,
            )
        ]

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""

        return LOGGER

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""

        return self.async_show_menu(
            step_id="user",
            menu_options=["keys", "pick_implementation"],
        )

    async def async_step_keys(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle direct credential input."""

        errors: dict[str, str] = {}
        if user_input is not None:
            uid = user_input[CONF_UID].strip()
            if not _UID_RE.match(uid):
                errors["base"] = "invalid_uid"
            else:
                data = {
                    CONF_UID: uid,
                    CONF_REGION: BEMFA_REGION,
                    CONF_AUTH_MODE: AUTH_MODE_KEYS,
                }
                return await self._async_create_bemfa_entry(data)

        return self.async_show_form(
            step_id="keys",
            data_schema=STEP_KEYS_SCHEMA,
            errors=errors,
            last_step=True,
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> FlowResult:
        """Create an entry after OAuth finishes."""

        token = data.get("token", {})
        uid = self._extract_uid_from_token(token)
        if not uid:
            return self.async_abort(reason="oauth_missing_uid")
        uid = str(uid)

        entry_data = {
            CONF_UID: uid,
            CONF_REGION: BEMFA_REGION,
            CONF_AUTH_MODE: AUTH_MODE_OAUTH,
        }
        return await self._async_create_bemfa_entry(entry_data)

    async def _async_create_bemfa_entry(self, data: dict[str, Any]) -> FlowResult:
        uid_md5 = hashlib.md5(data[CONF_UID].encode("utf-8")).hexdigest()
        await self.async_set_unique_id(uid_md5)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Bemfa Cloud", data=data)

    @classmethod
    def _extract_uid_from_token(cls, token: dict[str, Any]) -> str | None:
        """Extract the Bemfa private key from BeHome OAuth token data."""

        for key in ("uid", "open_id", "private_key", "bemfa_uid"):
            value = str(token.get(key, ""))
            if _UID_RE.match(value):
                return value

        access_token = str(token.get("access_token", ""))
        candidates = [
            access_token,
            access_token[4:-4] if len(access_token) > 8 else "",
        ]
        for candidate in candidates:
            if _UID_RE.match(candidate):
                return candidate
        return None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "OptionsFlowHandler":
        """Create the options flow."""

        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Bemfa Cloud options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""

        self._entry_id = config_entry.entry_id
        self._config: dict[str, dict[str, str]] = (
            config_entry.options.get(OPTIONS_CONFIG, {}).copy()
        )
        self._sync_dict: dict[str, Sync] = {}
        self._sync: Sync | None = None
        self._is_create = True

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show option menu."""

        return self.async_show_menu(
            step_id="init",
            menu_options=["create_all_syncs", "create_sync", "modify_sync", "destroy_sync"],
        )

    async def async_step_create_all_syncs(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create many syncs at once."""

        service = self._get_service()
        if user_input is not None:
            syncs = [self._sync_dict[entity_id] for entity_id in user_input[OPTIONS_SELECT]]
            try:
                await service.async_create_syncs(syncs)
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("Failed to create Bemfa syncs: %s", err)
                return self.async_show_form(
                    step_id="create_all_syncs",
                    data_schema=self._create_all_syncs_schema(),
                    errors={"base": ERROR_CANNOT_SYNC},
                )
            for sync in syncs:
                self._config[sync.topic] = sync.config.copy()
            return self.async_create_entry(title="", data={OPTIONS_CONFIG: self._config})

        self._sync_dict = self._collect_batchable_syncs()
        if not self._sync_dict:
            return self.async_show_form(step_id="empty", last_step=False)

        return self.async_show_form(
            step_id="create_all_syncs",
            data_schema=self._create_all_syncs_schema(),
        )

    async def async_step_create_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create one sync with detailed options."""

        if user_input is not None:
            self._sync = self._sync_dict[user_input[OPTIONS_SELECT]]
            self._is_create = True
            return await self._async_step_sync_config()

        self._sync_dict = self._collect_unconfigured_syncs()
        if not self._sync_dict:
            return self.async_show_form(step_id="empty", last_step=False)

        return self.async_show_form(
            step_id="create_sync",
            data_schema=vol.Schema(
                {
                    vol.Required(OPTIONS_SELECT): SelectSelector(
                        SelectSelectorConfig(
                            options=self._options_from_syncs(self._sync_dict),
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_modify_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Modify a configured sync."""

        if user_input is not None:
            self._sync = self._sync_dict[user_input[OPTIONS_SELECT]]
            self._is_create = False
            return await self._async_step_sync_config()

        self._sync_dict = self._collect_configured_syncs()
        if not self._sync_dict:
            return self.async_show_form(step_id="empty", last_step=False)

        return self.async_show_form(
            step_id="modify_sync",
            data_schema=vol.Schema(
                {
                    vol.Required(OPTIONS_SELECT): SelectSelector(
                        SelectSelectorConfig(
                            options=self._options_from_syncs(self._sync_dict),
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def _async_step_sync_config(self) -> FlowResult:
        assert self._sync is not None
        if self._sync.topic in self._config:
            self._sync.config = self._config[self._sync.topic].copy()
            self._sync.name = self._sync.config.get(OPTIONS_NAME, self._sync.name)
        return self.async_show_form(
            step_id=self._sync.get_config_step_id(),
            data_schema=vol.Schema(self._sync.generate_details_schema()),
        )

    async def async_step_sync_config_sensor(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_binary_sensor(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_climate(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_cover(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_fan(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_light(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_switch(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_outlet(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_thermostat(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_water_heater(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_tv(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_air_purifier(self, user_input=None) -> FlowResult:
        return await self._async_step_sync_config_done(user_input)

    async def _async_step_sync_config_done(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        assert self._sync is not None
        if user_input is None:
            return await self._async_step_sync_config()

        service = self._get_service()
        try:
            if self._is_create:
                await service.async_create_sync(self._sync, user_input)
            else:
                await service.async_modify_sync(self._sync, user_input)
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Failed to save Bemfa sync for %s: %s", self._sync.entity_id, err)
            return self.async_show_form(
                step_id=self._sync.get_config_step_id(),
                data_schema=vol.Schema(self._sync.generate_details_schema()),
                errors={"base": ERROR_CANNOT_SYNC},
            )

        self._config[self._sync.topic] = self._sync.config.copy()
        return self.async_create_entry(title="", data={OPTIONS_CONFIG: self._config})

    async def async_step_destroy_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Destroy local syncs."""

        service = self._get_service()
        if user_input is not None:
            for topic in user_input[OPTIONS_SELECT]:
                await service.async_destroy_sync(topic)
                self._config.pop(topic, None)
            return self.async_create_entry(title="", data={OPTIONS_CONFIG: self._config})

        topic_options = [
            SelectOptionDict(value=topic, label=config.get(OPTIONS_NAME, topic))
            for topic, config in self._config.items()
        ]
        if not topic_options:
            return self.async_show_form(step_id="empty", last_step=False)

        return self.async_show_form(
            step_id="destroy_sync",
            data_schema=vol.Schema(
                {
                    vol.Required(OPTIONS_SELECT): SelectSelector(
                        SelectSelectorConfig(
                            options=topic_options,
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
        )

    async def async_step_empty(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """No data found."""

        return await self.async_step_init(user_input)

    def _collect_unconfigured_syncs(self) -> dict[str, Sync]:
        return {
            sync.entity_id: sync
            for sync in self._get_service().collect_supported_syncs()
            if sync.topic not in self._config
        }

    def _collect_batchable_syncs(self) -> dict[str, Sync]:
        return {
            entity_id: sync
            for entity_id, sync in self._collect_unconfigured_syncs().items()
            if sync.get_config_step_id() != "sync_config_sensor"
        }

    def _collect_configured_syncs(self) -> dict[str, Sync]:
        result = {}
        for sync in self._get_service().collect_supported_syncs():
            if sync.topic in self._config:
                sync.config = self._config[sync.topic].copy()
                sync.name = sync.config.get(OPTIONS_NAME, sync.name)
                result[sync.entity_id] = sync
        return result

    @staticmethod
    def _options_from_syncs(syncs: dict[str, Sync]) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(value=sync.entity_id, label=sync.generate_option_label())
            for sync in syncs.values()
        ]

    def _create_all_syncs_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(OPTIONS_SELECT): SelectSelector(
                    SelectSelectorConfig(
                        options=self._options_from_syncs(self._sync_dict),
                        mode=SelectSelectorMode.LIST,
                        multiple=True,
                    )
                )
            }
        )

    def _get_service(self):
        return self.hass.data[DOMAIN][self._entry_id]["service"]
