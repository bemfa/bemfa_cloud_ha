"""Config flow for Bemfa Cloud."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers import entity_registry

from .const import (
    AUTH_MODE_KEYS,
    AUTH_MODE_OAUTH,
    AUTH_MODE_WECHAT_SCAN,
    CONF_AUTH_MODE,
    CONF_REGION,
    CONF_UID,
    BEMFA_REGION,
    DOMAIN,
    LOGGER,
    OPTIONS_CONFIG,
    OPTIONS_NAME,
    OPTIONS_SELECT,
    WECHAT_LOGIN_POLL_URL,
    WECHAT_QR_IMAGE_URL,
    WECHAT_QR_URL,
)
from .sync import Sync

ERROR_CANNOT_SYNC = "cannot_sync"
ERROR_WECHAT_NOT_SCANNED = "wechat_not_scanned"
ERROR_WECHAT_QR_FAILED = "wechat_qr_failed"
ERROR_WECHAT_LOGIN_FAILED = "wechat_login_failed"
WECHAT_LOGIN_TIMEOUT = 120
WECHAT_LOGIN_POLL_INTERVAL = 3
BATCH_PRIMARY_DOMAINS = {
    "climate",
    "cover",
    "fan",
    "humidifier",
    "light",
    "lock",
    "media_player",
    "vacuum",
    "water_heater",
}
BATCH_STANDALONE_DOMAINS = {
    "automation",
    "camera",
    "group",
    "input_boolean",
    "remote",
    "scene",
    "script",
    "siren",
    "switch",
}

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

    def __init__(self) -> None:
        """Initialize the flow."""

        super().__init__()
        self._wechat_sid: str | None = None
        self._wechat_qr_image_url: str | None = None
        self._wechat_login_task: asyncio.Task[dict[str, Any] | None] | None = None
        self._wechat_login_data: dict[str, Any] | None = None
        self._pending_entry_data: dict[str, Any] | None = None

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""

        return LOGGER

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""

        return self.async_show_menu(
            step_id="user",
            menu_options=["wechat_scan", "keys", "pick_implementation"],
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
                return await self._async_show_setup_next(data)

        return self.async_show_form(
            step_id="keys",
            data_schema=STEP_KEYS_SCHEMA,
            errors=errors,
            last_step=True,
        )

    async def async_step_wechat_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle WeChat QR code login."""

        if not self._wechat_sid or not self._wechat_qr_image_url:
            try:
                await self._async_prepare_wechat_qr()
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("Failed to prepare WeChat login QR code: %s", err)
                return self.async_abort(reason=ERROR_WECHAT_QR_FAILED)

        if self._wechat_login_task is None:
            self._wechat_login_task = self.hass.async_create_task(
                self._async_wait_for_wechat_login()
            )

        if not self._wechat_login_task.done():
            return self.async_show_progress(
                step_id="wechat_scan",
                progress_action="wechat_scan",
                description_placeholders={
                    "qr_image": self._wechat_qr_image_url or "",
                },
                progress_task=self._wechat_login_task,
            )

        try:
            self._wechat_login_data = await self._wechat_login_task
        except TimeoutError:
            self._wechat_login_task = None
            self._wechat_sid = None
            self._wechat_qr_image_url = None
            return self.async_show_progress_done(next_step_id="wechat_timeout")
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Failed to complete WeChat login: %s", err)
            self._wechat_login_task = None
            return self.async_show_progress_done(next_step_id="wechat_failed")

        self._wechat_login_task = None
        if self._wechat_login_data is None:
            return self.async_show_progress_done(next_step_id="wechat_timeout")
        return self.async_show_progress_done(next_step_id="wechat_done")

    async def async_step_wechat_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create an entry after WeChat QR code login finishes."""

        if not self._wechat_login_data:
            return self.async_abort(reason=ERROR_WECHAT_LOGIN_FAILED)

        uid = self._extract_uid_from_wechat_login(self._wechat_login_data)
        if not uid:
            return self.async_abort(reason=ERROR_WECHAT_LOGIN_FAILED)

        entry_data = {
            CONF_UID: uid,
            CONF_REGION: BEMFA_REGION,
            CONF_AUTH_MODE: AUTH_MODE_WECHAT_SCAN,
        }
        return await self._async_show_setup_next(entry_data)

    async def async_step_wechat_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle WeChat QR code login timeout."""

        return self.async_abort(reason=ERROR_WECHAT_NOT_SCANNED)

    async def async_step_wechat_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle WeChat QR code login failure."""

        return self.async_abort(reason=ERROR_WECHAT_LOGIN_FAILED)

    async def _async_wait_for_wechat_login(self) -> dict[str, Any] | None:
        """Poll until WeChat QR code login succeeds or times out."""

        async with asyncio.timeout(WECHAT_LOGIN_TIMEOUT):
            while True:
                data = await self._async_poll_wechat_login()
                if data is not None:
                    return data
                await asyncio.sleep(WECHAT_LOGIN_POLL_INTERVAL)

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
        return await self._async_show_setup_next(entry_data)

    async def _async_show_setup_next(self, data: dict[str, Any]) -> FlowResult:
        """Show the final setup instruction before creating an entry."""

        uid = data[CONF_UID]
        uid_md5 = hashlib.md5(uid.encode("utf-8")).hexdigest()
        await self.async_set_unique_id(uid_md5)
        self._abort_if_unique_id_configured()
        self._pending_entry_data = data
        return await self.async_step_setup_next()

    async def async_step_setup_next(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm the next step before creating the entry."""

        if user_input is not None and self._pending_entry_data is not None:
            return self._async_create_bemfa_entry(self._pending_entry_data)

        return self.async_show_form(
            step_id="setup_next",
            data_schema=vol.Schema({}),
            last_step=True,
        )

    def _async_create_bemfa_entry(self, data: dict[str, Any]) -> FlowResult:
        uid = data[CONF_UID]
        return self.async_create_entry(
            title=f"Bemfa Cloud ({uid[-6:]})",
            data=data,
        )

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

    @classmethod
    def _extract_uid_from_wechat_login(cls, data: dict[str, Any]) -> str | None:
        """Extract the Bemfa private key from WeChat login data."""

        open_id = str(data.get("openID", ""))
        if len(open_id) <= 6:
            return None

        # web_v2_user stores openID.substring(1, len - 2) as u_eml.
        # Other pages then decode u_eml.substring(1, len - 2) with Base64
        # to get the real Bemfa private key.
        encoded_uid = open_id[1:-2][1:-2]
        try:
            uid = base64.b64decode(
                encoded_uid + "=" * ((4 - len(encoded_uid) % 4) % 4)
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None

        return uid if _UID_RE.match(uid) else None

    async def _async_prepare_wechat_qr(self) -> None:
        """Fetch a WeChat QR code ticket and event key."""

        session = async_get_clientsession(self.hass)
        async with session.get(WECHAT_QR_URL, timeout=30) as response:
            data = await response.json(content_type=None)

        if response.status >= 400 or data.get("code") not in (0, None):
            raise ValueError(f"Unexpected WeChat QR response: {data}")

        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        ticket = str(payload.get("url") or "")
        sid = str(payload.get("sid") or "")
        if not ticket or not sid:
            raise ValueError(f"WeChat QR response missing ticket or sid: {data}")

        self._wechat_sid = sid
        self._wechat_qr_image_url = WECHAT_QR_IMAGE_URL.format(ticket=ticket)

    async def _async_poll_wechat_login(self) -> dict[str, Any] | None:
        """Poll whether the current WeChat QR code has been scanned."""

        if not self._wechat_sid:
            return None

        session = async_get_clientsession(self.hass)
        async with session.post(
            WECHAT_LOGIN_POLL_URL,
            json={"eventKey": self._wechat_sid},
            timeout=30,
        ) as response:
            data = await response.json(content_type=None)

        if response.status >= 400:
            raise ValueError(f"Unexpected WeChat login response: {data}")

        if data.get("code") != 0:
            return None

        payload = data.get("data") if isinstance(data.get("data"), dict) else None
        if not payload or payload.get("code") != 0:
            return None
        return payload

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

        if user_input is not None:
            syncs = [self._sync_dict[entity_id] for entity_id in user_input[OPTIONS_SELECT]]
            for sync in syncs:
                sync.config = {OPTIONS_NAME: sync.name}
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

        self._sync.name = user_input.get(OPTIONS_NAME, self._sync.name)
        self._sync.config = user_input.copy()
        self._config[self._sync.topic] = self._sync.config.copy()
        return self.async_create_entry(title="", data={OPTIONS_CONFIG: self._config})

    async def async_step_destroy_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Destroy local syncs."""

        if user_input is not None:
            for topic in user_input[OPTIONS_SELECT]:
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
            if self._is_recommended_batch_sync(sync)
        }

    def _is_recommended_batch_sync(self, sync: Sync) -> bool:
        """Return whether a sync should appear in the bulk setup list."""

        if sync.get_config_step_id() == "sync_config_sensor":
            return False

        domain = sync.entity_id.split(".", 1)[0]
        if domain in BATCH_PRIMARY_DOMAINS:
            return True
        if domain not in BATCH_STANDALONE_DOMAINS:
            return False

        entity_reg = entity_registry.async_get(self.hass)
        entry = entity_reg.async_get(sync.entity_id)
        if entry is None or entry.device_id is None:
            return True

        for device_entry in entity_registry.async_entries_for_device(
            entity_reg, entry.device_id
        ):
            device_domain = device_entry.entity_id.split(".", 1)[0]
            if device_domain in BATCH_PRIMARY_DOMAINS:
                return False
        return True

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
