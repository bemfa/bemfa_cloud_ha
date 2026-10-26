"""TCP JSON long connection for Bemfa Cloud."""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from .const import (
    LOGGER,
    TCP_CONNECT_TIMEOUT,
    TCP_HOST,
    TCP_PING_INTERVAL,
    TCP_PORT,
    TCP_RECONNECT_DELAY,
    TCP_RESUBSCRIBE_INTERVAL,
    TCP_TLS_SHUTDOWN_TIMEOUT,
)
from .sync import Sync

STATE_PUBLISH_DELAY = 0.2
CONTROL_FEEDBACK_DELAY = 1.0
MAX_PUBLISH_DELAY = 2.0


class BemfaCloudTcp:
    """Maintain Bemfa TCP JSON subscriptions and state publishing."""

    def __init__(self, hass: HomeAssistant, uid: str) -> None:
        """Initialize the TCP client."""

        self._hass = hass
        self._uid = uid
        self._topic_to_sync: dict[str, Sync] = {}
        self._writer: asyncio.StreamWriter | None = None
        self._runner: asyncio.Task[None] | None = None
        self._ssl_context: ssl.SSLContext | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._remove_listener: CALLBACK_TYPE | None = None
        self._publish_tasks: dict[str, asyncio.Task[None]] = {}
        self._publish_deadlines: dict[str, float] = {}
        self._publish_max_deadlines: dict[str, float] = {}
        self._last_published: dict[str, str] = {}
        self._publish_lock = asyncio.Lock()
        self._stopped = asyncio.Event()
        self._stopped.set()

    async def async_start(self) -> None:
        """Start connection management."""

        async with self._lifecycle_lock:
            if self._runner is not None:
                return

            # Loading the system CA certificates can block; keep it off HA's loop.
            self._ssl_context = await self._hass.async_add_executor_job(
                ssl.create_default_context
            )
            self._ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
            self._stopped.clear()
            self._remove_listener = self._hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._state_listener
            )
            self._runner = self._hass.async_create_background_task(
                self._run(), "bemfa_cloud_tcp"
            )

    async def async_stop(self) -> None:
        """Stop the connection."""

        async with self._lifecycle_lock:
            self._stopped.set()
            if self._remove_listener is not None:
                self._remove_listener()
                self._remove_listener = None
            tasks = list(self._publish_tasks.values())
            if self._runner is not None:
                tasks.append(self._runner)
            for task in tasks:
                task.cancel()
            await asyncio.gather(
                *tasks,
                *(sync.async_stop() for sync in self._topic_to_sync.values()),
                return_exceptions=True,
            )
            self._runner = None
            self._publish_tasks.clear()
            self._publish_deadlines.clear()
            self._publish_max_deadlines.clear()
            self._topic_to_sync.clear()
            await self._close_writer()

    async def async_add_sync(self, sync: Sync) -> None:
        """Add a sync and publish its current state."""

        await self.async_add_syncs([sync])

    async def async_add_syncs(self, syncs: list[Sync]) -> None:
        """Add syncs, subscribe their topics in one TCP command, and publish state."""

        if self._stopped.is_set():
            return
        topics: list[str] = []
        for sync in syncs:
            old_sync = self._topic_to_sync.get(sync.topic)
            if old_sync is not None and old_sync is not sync:
                await old_sync.async_stop()
            if self._stopped.is_set():
                return
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

        if self._stopped.is_set():
            return
        await self.async_add_sync(sync)

    async def async_remove_sync(self, topic: str) -> None:
        """Remove a sync locally."""

        sync = self._topic_to_sync.pop(topic, None)
        task = self._publish_tasks.pop(topic, None)
        self._publish_deadlines.pop(topic, None)
        self._publish_max_deadlines.pop(topic, None)
        self._last_published.pop(topic, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if sync is not None:
            await sync.async_stop()
        await self._close_writer()

    async def async_publish_sync(self, sync: Sync) -> None:
        """Publish the current Home Assistant state for a sync."""

        # Serialize comparison and writing so concurrent publishers cannot both
        # send the same state before either has updated the cache.
        async with self._publish_lock:
            if (
                self._stopped.is_set()
                or self._topic_to_sync.get(sync.topic) is not sync
            ):
                return
            msg = sync.generate_msg()
            if not msg:
                return
            content = json.dumps(
                msg, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if self._last_published.get(sync.topic) == content:
                return
            writer = self._writer
            if (
                await self._send(
                    {
                        "cmd": 2,
                        "uid": self._uid,
                        "topics": [sync.topic],
                        "msg": msg,
                        "mode": 3,
                    }
                )
                and self._writer is writer
            ):
                self._last_published[sync.topic] = content

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        TCP_HOST,
                        TCP_PORT,
                        ssl=self._ssl_context,
                        server_hostname=TCP_HOST,
                        ssl_handshake_timeout=TCP_CONNECT_TIMEOUT,
                        ssl_shutdown_timeout=TCP_TLS_SHUTDOWN_TIMEOUT,
                    ),
                    timeout=TCP_CONNECT_TIMEOUT,
                )
                self._writer = writer
                self._last_published.clear()
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
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=TCP_RECONNECT_DELAY
                )
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
        if self._stopped.is_set():
            return
        topics = payload.get("topics") or []
        if not topics:
            return
        topic = topics[0]
        sync = self._topic_to_sync.get(topic)
        if sync is None or "msg" not in payload:
            return
        msg = payload["msg"]
        LOGGER.debug(
            "Bemfa TCP received topic=%s msg=%s", topic, self._msg_to_text(msg)
        )
        sync.resolve_msg(msg)
        self._schedule_publish(sync, CONTROL_FEEDBACK_DELAY)

    def _schedule_publish(self, sync: Sync, delay: float) -> None:
        """Merge state events and control feedback, with a bounded wait."""

        if self._stopped.is_set() or self._topic_to_sync.get(sync.topic) is not sync:
            return
        topic = sync.topic
        now = self._hass.loop.time()
        max_deadline = self._publish_max_deadlines.setdefault(
            topic, now + MAX_PUBLISH_DELAY
        )
        self._publish_deadlines[topic] = min(
            max(self._publish_deadlines.get(topic, 0), now + delay), max_deadline
        )
        if topic not in self._publish_tasks:
            self._publish_tasks[topic] = self._hass.async_create_background_task(
                self._async_publish_pending(topic), "bemfa_cloud_state_publish"
            )

    async def _async_publish_pending(self, topic: str) -> None:
        """Publish the latest HA state, including changes arriving during a send."""

        try:
            while not self._stopped.is_set() and topic in self._topic_to_sync:
                deadline = self._publish_deadlines.get(topic)
                if deadline is None:
                    return
                remaining = deadline - self._hass.loop.time()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                self._publish_deadlines.pop(topic, None)
                self._publish_max_deadlines.pop(topic, None)
                await self.async_publish_sync(self._topic_to_sync[topic])
        finally:
            if self._publish_tasks.get(topic) is asyncio.current_task():
                self._publish_tasks.pop(topic, None)
                self._publish_deadlines.pop(topic, None)
                self._publish_max_deadlines.pop(topic, None)

    async def _state_listener(self, event) -> None:
        if self._stopped.is_set():
            return
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        entity_id = new_state.entity_id
        for sync in list(self._topic_to_sync.values()):
            if entity_id in sync.get_watched_entity_ids():
                self._schedule_publish(sync, STATE_PUBLISH_DELAY)

    async def _subscribe(self, topics: list[str]) -> bool:
        if topics:
            LOGGER.info("Bemfa TCP subscribe topics: %s", topics)
            if not await self._send(
                {"cmd": 1, "uid": self._uid, "topics": topics, "mode": 0}
            ):
                await self._close_writer()
                return False
        return True

    async def _publish_all(self) -> None:
        for sync in list(self._topic_to_sync.values()):
            await self.async_publish_sync(sync)

    async def _send(self, payload: dict[str, Any]) -> bool:
        writer = self._writer
        if self._stopped.is_set() or writer is None or writer.is_closing():
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
            if self._writer is writer:
                await self._close_writer()
            return False
        return (
            not self._stopped.is_set()
            and self._writer is writer
            and not writer.is_closing()
        )

    async def _close_writer(self) -> None:
        writer = self._writer
        self._writer = None
        self._last_published.clear()
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
