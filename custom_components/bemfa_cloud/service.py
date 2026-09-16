"""Bemfa Cloud sync service."""

from __future__ import annotations

import asyncio

from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry
from homeassistant.helpers.start import async_at_started

from .const import EXCLUDED_SOURCE_PLATFORMS, LOGGER, OPTIONS_NAME
from .http import BemfaCloudApiError, BemfaCloudHttp, TopicPayload
from .sync import SYNC_TYPES, Sync
from .tcp import BemfaCloudTcp

MAX_TOPIC_LENGTH = 48
MAX_ROOM_LENGTH = 32
MAX_GROUP_LENGTH = 32
RESTORE_RETRY_DELAYS = (1, 2, 4)


class BemfaCloudService:
    """Manage topic creation and TCP synchronization."""

    def __init__(self, hass: HomeAssistant, credentials: dict[str, str]) -> None:
        """Initialize the service."""

        self._hass = hass
        self._http = BemfaCloudHttp(hass, credentials)
        self._tcp = BemfaCloudTcp(hass, credentials["uid"])
        self._config: dict[str, dict[str, str]] = {}
        self._syncs_by_entity_id: dict[str, Sync] = {}
        self._unsub_registry_listeners: list[CALLBACK_TYPE] = []

    async def async_start(self, config: dict[str, dict[str, str]]) -> None:
        """Start service and restore configured syncs."""

        self._config = config
        await self._tcp.async_start()

        async def _start(hass: HomeAssistant) -> None:
            await self._async_restore_syncs_with_retry()

        # The entity registry must be fully loaded before topic hashes are
        # calculated. Otherwise a restart can derive one topic from entity_id
        # and another from unique_id for the same HA entity.
        async_at_started(self._hass, _start)

        self._start_registry_listeners()

    async def _async_restore_syncs_with_retry(self) -> None:
        """Restore syncs with bounded retries and no unhandled task exception."""

        for attempt, delay in enumerate((*RESTORE_RETRY_DELAYS, None), start=1):
            try:
                await self._async_restore_syncs()
                return
            except Exception as err:  # noqa: BLE001
                if delay is None:
                    LOGGER.error(
                        "Bemfa sync restore failed after %s attempts: %s", attempt, err
                    )
                    return
                LOGGER.warning(
                    "Bemfa sync restore attempt %s failed; retrying in %ss: %s",
                    attempt,
                    delay,
                    err,
                )
                await asyncio.sleep(delay)

    async def async_stop(self) -> None:
        """Stop service."""

        for unsub in self._unsub_registry_listeners:
            unsub()
        self._unsub_registry_listeners.clear()
        await self._tcp.async_stop()

    async def _async_restore_syncs(self) -> None:
        syncs = []
        for sync in self.collect_supported_syncs():
            if sync.topic not in self._config:
                continue
            sync.config = self._config.get(sync.topic, {OPTIONS_NAME: sync.name}).copy()
            sync.name = sync.config.get(OPTIONS_NAME, sync.name)
            syncs.append(sync)

        ready_syncs = await self._ensure_topics(syncs)
        await self._tcp.async_add_syncs(ready_syncs)
        self._syncs_by_entity_id = {
            sync.entity_id: sync for sync in ready_syncs
        }

    def collect_supported_syncs(self) -> list[Sync]:
        """Collect all supported HA syncs."""

        syncs: list[Sync] = []
        for sync_type in SYNC_TYPES.values():
            try:
                syncs.extend(sync_type.collect_supported_syncs(self._hass))
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("Failed to collect %s syncs: %s", sync_type.__name__, err)

        syncs = [sync for sync in syncs if not self._is_excluded_sync(sync)]
        covered_entity_ids = {sync.entity_id for sync in syncs}
        syncs.extend(self._collect_fallback_switch_syncs(covered_entity_ids))
        return sorted(syncs, key=lambda item: item.entity_id)

    _FALLBACK_EXCLUDED_DOMAINS: frozenset[str] = frozenset(
        {"automation", "script", "scene", "group"}
    )

    def _collect_fallback_switch_syncs(self, covered_entity_ids: set[str]) -> list[Sync]:
        """Map unrecognized turnable entities to Bemfa switch devices."""

        from .sync_switch import Switch

        fallback_syncs: list[Sync] = []
        for state in self._hass.states.async_all():
            try:
                if state.entity_id in covered_entity_ids:
                    continue
                if self._is_excluded_entity_id(state.entity_id):
                    continue

                domain = state.entity_id.split(".", 1)[0]
                if domain in self._FALLBACK_EXCLUDED_DOMAINS:
                    continue
                if not (
                    self._hass.services.has_service(domain, SERVICE_TURN_ON)
                    and self._hass.services.has_service(domain, SERVICE_TURN_OFF)
                ):
                    continue

                fallback_syncs.append(Switch(self._hass, state.entity_id, state.name))
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("Failed to collect fallback sync for %s: %s", state.entity_id, err)
        return fallback_syncs

    async def async_create_sync(self, sync: Sync, user_input: dict[str, str]) -> None:
        """Create one sync."""

        sync.name = user_input.get(OPTIONS_NAME, sync.name)
        sync.config = user_input.copy()
        ready_syncs = await self._ensure_topics([sync])
        if ready_syncs:
            await self._tcp.async_add_sync(sync)
            self._syncs_by_entity_id[sync.entity_id] = sync

    async def async_create_syncs(self, syncs: list[Sync]) -> None:
        """Create multiple syncs with default names."""

        for sync in syncs:
            sync.config = {OPTIONS_NAME: sync.name}
        ready_syncs = await self._ensure_topics(syncs)
        await self._tcp.async_add_syncs(ready_syncs)
        self._syncs_by_entity_id.update(
            {sync.entity_id: sync for sync in ready_syncs}
        )

    async def async_modify_sync(self, sync: Sync, user_input: dict[str, str]) -> None:
        """Modify sync configuration and publish the latest state."""

        sync.name = user_input.get(OPTIONS_NAME, sync.name)
        sync.config = user_input.copy()
        ready_syncs = await self._ensure_topics([sync])
        if ready_syncs:
            await self._tcp.async_update_sync(sync)
            self._syncs_by_entity_id[sync.entity_id] = sync

    async def async_destroy_sync(self, topic: str) -> None:
        """Remove a local sync."""

        self._syncs_by_entity_id = {
            entity_id: sync
            for entity_id, sync in self._syncs_by_entity_id.items()
            if sync.topic != topic
        }
        await self._tcp.async_remove_sync(topic)

    async def _ensure_topics(self, syncs: list[Sync]) -> list[Sync]:
        payloads = [
            TopicPayload(topic=sync.topic, name=sync.name, room=self._sync_room(sync))
            for sync in syncs
        ]
        failures: dict[str, str] = {}
        valid_payloads: list[TopicPayload] = []
        for payload in payloads:
            if reason := self._topic_payload_validation_error(payload):
                failures[payload.topic] = reason
            else:
                valid_payloads.append(payload)

        try:
            batch_failures = await self._http.async_create_topics(valid_payloads)
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Bemfa batch topic creation failed; retrying topics individually: %s",
                err,
            )
            failures.update(
                await self._async_retry_topics_individually(valid_payloads)
            )
        else:
            if batch_failures:
                LOGGER.warning(
                    "Bemfa batch reported failed topics; confirming them individually: %s",
                    batch_failures,
                )
                failed_topics = set(batch_failures)
                failures.update(
                    await self._async_retry_topics_individually(
                        [
                            payload
                            for payload in valid_payloads
                            if payload.topic in failed_topics
                        ]
                    )
                )

        if failures:
            LOGGER.warning("Skip Bemfa syncs with confirmed creation failures: %s", failures)
        return [sync for sync in syncs if sync.topic not in failures]

    @staticmethod
    def _topic_payload_validation_error(payload: TopicPayload) -> str | None:
        """Return a local validation error matching the Bemfa API constraints."""

        if not payload.topic:
            return "topic is required"
        if len(payload.topic) > MAX_TOPIC_LENGTH:
            return f"topic length exceeds {MAX_TOPIC_LENGTH}"
        if not payload.topic.isascii() or not payload.topic.isalnum():
            return "topic must contain only ASCII letters and numbers"
        if len(payload.room) > MAX_ROOM_LENGTH:
            return f"room length exceeds {MAX_ROOM_LENGTH}"
        if len(payload.group) > MAX_GROUP_LENGTH:
            return f"group length exceeds {MAX_GROUP_LENGTH}"
        return None

    async def _async_retry_topics_individually(
        self, payloads: list[TopicPayload]
    ) -> dict[str, str]:
        """Retry a rejected batch without blocking TCP restore for uncertain failures."""

        failures: dict[str, str] = {}
        for payload in payloads:
            try:
                failures.update(await self._http.async_create_topics([payload]))
            except BemfaCloudApiError as err:
                failures[payload.topic] = str(err)
            except Exception as err:  # noqa: BLE001
                LOGGER.warning(
                    "Unable to confirm Bemfa topic %s; continue TCP sync: %s",
                    payload.topic,
                    err,
                )
        return failures

    def _start_registry_listeners(self) -> None:
        """Listen for HA name and area changes and mirror them to Bemfa."""

        self._unsub_registry_listeners.append(
            self._hass.bus.async_listen(
                entity_registry.EVENT_ENTITY_REGISTRY_UPDATED,
                self._async_entity_registry_updated,
            )
        )
        self._unsub_registry_listeners.append(
            self._hass.bus.async_listen(
                device_registry.EVENT_DEVICE_REGISTRY_UPDATED,
                self._async_device_registry_updated,
            )
        )
        self._unsub_registry_listeners.append(
            self._hass.bus.async_listen(
                area_registry.EVENT_AREA_REGISTRY_UPDATED,
                self._async_area_registry_updated,
            )
        )

    async def _async_entity_registry_updated(self, event: Event) -> None:
        """Mirror HA entity name and room changes to Bemfa."""

        if event.data.get("action") != "update":
            return

        entity_id = event.data.get("entity_id")
        old_entity_id = event.data.get("old_entity_id")
        sync = self._syncs_by_entity_id.get(entity_id)
        if sync is None and old_entity_id:
            sync = self._syncs_by_entity_id.pop(old_entity_id, None)
            if sync is not None:
                sync._entity_id = entity_id
                self._syncs_by_entity_id[entity_id] = sync
        if sync is None:
            return
        if self._is_excluded_sync(sync):
            return

        changes = event.data.get("changes") or {}
        if any(key in changes for key in ("name", "name_by_user", "original_name")):
            await self._sync_bemfa_name(sync)

        if any(key in changes for key in ("area_id", "device_id")):
            await self._sync_bemfa_room(sync)

    async def _async_device_registry_updated(self, event: Event) -> None:
        """Mirror HA device room changes to all related Bemfa topics."""

        if event.data.get("action") != "update":
            return
        if "area_id" not in (event.data.get("changes") or {}):
            return

        device_id = event.data.get("device_id")
        entity_reg = entity_registry.async_get(self._hass)
        for entry in entity_registry.async_entries_for_device(entity_reg, device_id):
            if sync := self._syncs_by_entity_id.get(entry.entity_id):
                await self._sync_bemfa_room(sync)

    async def _async_area_registry_updated(self, event: Event) -> None:
        """Mirror HA area rename/removal to affected Bemfa topic rooms."""

        if event.data.get("action") not in ("update", "remove"):
            return

        area_id = event.data.get("area_id")
        for sync in self._syncs_by_entity_id.values():
            if self._sync_area_id(sync) == area_id:
                await self._sync_bemfa_room(sync)

    async def _sync_bemfa_name(self, sync: Sync) -> None:
        entry = entity_registry.async_get(self._hass).async_get(sync.entity_id)
        if entry is not None:
            name = entity_registry.async_get_full_entity_name(self._hass, entry)
        else:
            state = self._hass.states.get(sync.entity_id)
            name = state.name if state is not None else sync.name
        if name == sync.name:
            return
        sync.name = name
        try:
            await self._http.async_modify_name(sync.topic, name)
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Failed to sync Bemfa topic name for %s: %s", sync.topic, err)

    async def _sync_bemfa_room(self, sync: Sync) -> None:
        try:
            await self._http.async_modify_room([sync.topic], self._sync_room(sync))
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Failed to sync Bemfa topic room for %s: %s", sync.topic, err)

    def _sync_room(self, sync: Sync) -> str:
        """Return the HA area name that should be used as Bemfa room."""

        area_id = self._sync_area_id(sync)
        if area_id is None:
            return ""
        area = area_registry.async_get(self._hass).async_get_area(area_id)
        return area.name if area is not None else ""

    def _sync_area_id(self, sync: Sync) -> str | None:
        """Return entity or device area id for a sync."""

        entity_entry = entity_registry.async_get(self._hass).async_get(sync.entity_id)
        if entity_entry is None:
            return None
        if entity_entry.area_id:
            return entity_entry.area_id
        if entity_entry.device_id is None:
            return None
        device = device_registry.async_get(self._hass).async_get(entity_entry.device_id)
        return device.area_id if device is not None else None

    def _is_excluded_sync(self, sync: Sync) -> bool:
        """Return if a HA entity should never be mirrored to Bemfa."""

        return self._is_excluded_entity_id(sync.entity_id)

    def _is_excluded_entity_id(self, entity_id: str) -> bool:
        """Skip entities created by BeHome or by this integration."""

        entry = entity_registry.async_get(self._hass).async_get(entity_id)
        if entry is None:
            return False
        if entry.platform in EXCLUDED_SOURCE_PLATFORMS:
            return True
        return bool(entry.unique_id) and entry.unique_id.startswith(
            tuple(f"{platform}_" for platform in EXCLUDED_SOURCE_PLATFORMS)
        )
