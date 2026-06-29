"""TCP JSON long connection for Bemfa Cloud."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from .const import (
    CONF_UID,
    LOGGER,
    TCP_CONNECT_TIMEOUT,
    TCP_HOST,
    TCP_PING_INTERVAL,
    TCP_PORT,
    TCP_RECONNECT_DELAY,
    TCP_RESUBSCRIBE_INTERVAL,
)
from .sync import Sync


class BemfaCloudTcp:
    """Maintain Bemfa TCP JSON subscriptions and state publishing."""

    def __init__(self, hass: HomeAssistant, uid: str) -> None:
        """Initialize the TCP client."""

        self._hass = hass
        self._uid = uid
        self._topic_to_sync: dict[str, Sync] = {}
        self._writer: asyncio.StreamWriter | None = None
        self._runner: asyncio.Task[None] | None = None
        self._remove_listener: CALLBACK_TYPE | None = None
        self._feedback_suppressed_until: dict[str, float] = {}
        self._stopped = asyncio.Event()

    async def async_start(self) -> None:
        """Start connection management."""

        if self._runner is not None:
            return

        self._stopped.clear()
        self._remove_listener = self._hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._state_listener
        )
        self._runner = self._hass.async_create_background_task(
            self._run(), "bemfa_cloud_tcp"
        )

    async def async_stop(self) -> None:
        """Stop the connection."""

        self._stopped.set()
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
            self._runner = None
        await self._close_writer()

    async def async_add_sync(self, sync: Sync) -> None:
        """Add a sync and publish its current state."""

        await self.async_add_syncs([sync])

    async def async_add_syncs(self, syncs: list[Sync]) -> None:
        """Add syncs, subscribe their topics in one TCP command, and publish state."""

        topics: list[str] = []
        for sync in syncs:
            self._topic_to_sync[sync.topic] = sync
            topics.append(sync.topic)

        await self._subscribe(topics)
        for sync in syncs:
            await self.async_publish_sync(sync)

    async def async_subscribe_all(self) -> bool:
        """Subscribe all known topics on the current TCP connection."""

        return await self._subscribe(list(self._topic_to_sync))

    async def async_update_sync(self, sync: Sync) -> None:
        """Update a sync."""

        self._topic_to_sync[sync.topic] = sync
        await self.async_publish_sync(sync)

    async def async_remove_sync(self, topic: str) -> None:
        """Remove a sync locally."""

        self._topic_to_sync.pop(topic, None)
        await self._close_writer()

    async def async_publish_sync(self, sync: Sync) -> None:
        """Publish the current Home Assistant state for a sync."""

        msg = sync.generate_msg()
        if not msg:
            return
        await self._send(
            {
                "cmd": 2,
                "uid": self._uid,
                "topics": [sync.topic],
                "msg": msg,
                "mode": 3,
            }
        )

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(TCP_HOST, TCP_PORT),
                    timeout=TCP_CONNECT_TIMEOUT,
                )
                self._writer = writer
                await self._subscribe(list(self._topic_to_sync))
                await self._publish_all()
                await self._read_loop(reader)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("Bemfa TCP connection failed: %s", err)
            finally:
                await self._close_writer()

            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=TCP_RECONNECT_DELAY)
            except TimeoutError:
                continue

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        next_ping = self._hass.loop.time() + TCP_PING_INTERVAL
        next_resubscribe = self._hass.loop.time() + TCP_RESUBSCRIBE_INTERVAL
        while not self._stopped.is_set():
            now = self._hass.loop.time()
            if now >= next_resubscribe:
                if not await self.async_subscribe_all():
                    raise ConnectionError("Bemfa TCP resubscribe failed")
                next_resubscribe = self._hass.loop.time() + TCP_RESUBSCRIBE_INTERVAL

            timeout = max(1, min(next_ping, next_resubscribe) - now)
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
            except TimeoutError:
                if self._hass.loop.time() >= next_resubscribe:
                    if not await self.async_subscribe_all():
                        raise ConnectionError("Bemfa TCP resubscribe failed")
                    next_resubscribe = self._hass.loop.time() + TCP_RESUBSCRIBE_INTERVAL
                    continue
                if not await self._send({"cmd": 7, "uid": self._uid}):
                    raise ConnectionError("Bemfa TCP heartbeat failed")
                next_ping = self._hass.loop.time() + TCP_PING_INTERVAL
                continue

            if not raw:
                raise ConnectionError("Bemfa TCP connection closed")

            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                LOGGER.debug("Ignore non-json TCP payload: %r", raw)
                continue

            self._handle_payload(payload)

    def _handle_payload(self, payload: dict[str, Any]) -> None:
        topics = payload.get("topics") or []
        if not topics:
            return
        topic = topics[0]
        sync = self._topic_to_sync.get(topic)
        if sync is None or "msg" not in payload:
            return
        msg = payload["msg"]
        LOGGER.debug("Bemfa TCP received topic=%s msg=%s", topic, self._msg_to_text(msg))
        self._suppress_state_feedback(sync, seconds=2)
        sync.resolve_msg(msg)
        self._hass.async_create_task(
            self._async_publish_control_feedback(sync, msg),
            "bemfa_cloud_control_feedback",
        )

    def _suppress_state_feedback(self, sync: Sync, seconds: int) -> None:
        until = self._hass.loop.time() + seconds
        for entity_id in sync.get_watched_entity_ids():
            self._feedback_suppressed_until[entity_id] = until

    async def _async_publish_control_feedback(self, sync: Sync, msg: Any) -> None:
        """Publish a complete state payload shortly after a control command."""

        await asyncio.sleep(1)
        feedback = sync.generate_feedback_msg(msg)
        if not feedback:
            return
        await self._send(
            {
                "cmd": 2,
                "uid": self._uid,
                "topics": [sync.topic],
                "msg": feedback,
                "mode": 3,
            }
        )

    async def _state_listener(self, event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        entity_id = new_state.entity_id
        suppress_until = self._feedback_suppressed_until.get(entity_id, 0)
        if suppress_until > self._hass.loop.time():
            return
        self._feedback_suppressed_until.pop(entity_id, None)
        for sync in list(self._topic_to_sync.values()):
            if entity_id in sync.get_watched_entity_ids():
                await self.async_publish_sync(sync)

    async def _subscribe(self, topics: list[str]) -> bool:
        if topics:
            LOGGER.info("Bemfa TCP subscribe topics: %s", topics)
            if not await self._send({"cmd": 1, "uid": self._uid, "topics": topics, "mode": 0}):
                await self._close_writer()
                return False
        return True

    async def _publish_all(self) -> None:
        for sync in list(self._topic_to_sync.values()):
            await self.async_publish_sync(sync)

    async def _send(self, payload: dict[str, Any]) -> bool:
        writer = self._writer
        if writer is None or writer.is_closing():
            return False
        try:
            writer.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
                + b"\n"
            )
            await writer.drain()
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Bemfa TCP send failed: %s", err)
            await self._close_writer()
            return False
        return True

    async def _close_writer(self) -> None:
        writer = self._writer
        self._writer = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _msg_to_text(msg: Any) -> str:
        if isinstance(msg, str):
            return msg
        return json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
