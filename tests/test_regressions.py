"""Offline regression tests; HA interfaces are replaced, integration code is real.

Run with: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "bemfa_cloud"


def load_integration():
    """Import the integration with minimal HA interfaces, without installing HA."""
    modules = {}

    def module(name, **attrs):
        result = types.ModuleType(name)
        result.__dict__.update(attrs)
        modules[name] = result
        return result

    class Registry(dict):
        def register(self, name):
            def decorate(cls):
                self[name] = cls
                return cls

            return decorate

    module("homeassistant")
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EVENT_STATE_CHANGED="state_changed",
        ATTR_ENTITY_ID="entity_id",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
        SERVICE_TURN_ON="turn_on",
        SERVICE_TURN_OFF="turn_off",
    )
    module(
        "homeassistant.core",
        CALLBACK_TYPE=object,
        Event=object,
        HomeAssistant=object,
        callback=lambda func: func,
    )
    module("homeassistant.components", persistent_notification=SimpleNamespace())
    helpers = module("homeassistant.helpers")
    for name in ("area_registry", "device_registry", "entity_registry"):
        registry = module(
            f"homeassistant.helpers.{name}",
            **{f"EVENT_{name.upper()}_UPDATED": f"{name}_updated"},
        )
        setattr(helpers, name, registry)
    module("homeassistant.helpers.start", async_at_started=lambda *args: lambda: None)
    module(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: None,
    )
    module("homeassistant.util")
    module("homeassistant.util.decorator", Registry=Registry)
    module("homeassistant.util.read_only_dict", ReadOnlyDict=dict)
    module("voluptuous")
    for name in (
        "binary_sensor",
        "climate",
        "cover",
        "fan",
        "light",
        "sensor",
        "switch",
        "water_heater",
    ):
        module(f"offline_bemfa.sync_{name}")

    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(
            "offline_bemfa", COMPONENT / "__init__.py"
        )
        integration = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = integration
        spec.loader.exec_module(integration)
        return SimpleNamespace(
            integration=integration,
            service=sys.modules["offline_bemfa.service"],
            tcp=sys.modules["offline_bemfa.tcp"],
            sync=sys.modules["offline_bemfa.sync"],
        )


CODE = load_integration()


class Bus:
    def __init__(self):
        self.listeners = []

    def async_listen(self, name, listener):
        item = (name, listener)
        self.listeners.append(item)
        return lambda: self.listeners.remove(item)


class Hass:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.bus = Bus()
        self.data = {}
        self.tasks = []

    def async_create_task(self, coro, *args):
        # HA starts tasks eagerly; exercise that behavior rather than relying
        # on the task only beginning on the next event loop iteration.
        task = asyncio.Task(coro, loop=self.loop, eager_start=True)
        self.tasks.append(task)
        return task

    async_create_background_task = async_create_task

    async def async_add_executor_job(self, target, *args):
        return await asyncio.to_thread(target, *args)


class Writer:
    def __init__(self, *, fail=False):
        self.messages = []
        self.closed = False
        self.fail = fail
        self.gate = None
        self.written = asyncio.Event()

    def write(self, raw):
        self.messages.append(json.loads(raw))
        self.written.set()

    async def drain(self):
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ConnectionError("simulated write failure")

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class Device(CODE.sync.ControllableSync):
    @staticmethod
    def get_config_step_id():
        return "test"

    @staticmethod
    def _get_topic_suffix():
        return CODE.sync.TopicSuffix.CLIMATE

    @staticmethod
    def _supported_domain():
        return "climate"

    def __init__(self, hass, entity_id="climate.test"):
        super().__init__(hass, entity_id, entity_id)
        self._topic = entity_id.replace(".", "")
        self.payload = {"on": True, "t": 26}
        self.controls = []

    def _generate_msg_payload(self):
        return self.payload.copy()

    def resolve_msg(self, msg):
        self.controls.append(msg)


class TcpTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tls_files = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tls_files.cleanup)
        cls.cert_path = Path(cls.tls_files.name) / "cert.pem"
        cls.key_path = Path(cls.tls_files.name) / "key.pem"
        cls.has_openssl = shutil.which("openssl") is not None
        if cls.has_openssl:
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=tcp-cn-03.bemfa.com",
                    "-addext",
                    "subjectAltName=DNS:tcp-cn-03.bemfa.com",
                    "-keyout",
                    str(cls.key_path),
                    "-out",
                    str(cls.cert_path),
                ],
                check=True,
                capture_output=True,
            )

    async def asyncSetUp(self):
        self.timing = patch.multiple(
            CODE.tcp,
            STATE_PUBLISH_DELAY=0.01,
            CONTROL_FEEDBACK_DELAY=0.03,
            MAX_PUBLISH_DELAY=0.06,
        )
        self.timing.start()
        self.hass = Hass()
        self.tcp = CODE.tcp.BemfaCloudTcp(self.hass, "offline")
        self.tcp._stopped.clear()
        self.device = Device(self.hass)
        self.tcp._topic_to_sync[self.device.topic] = self.device
        self.writer = Writer()
        self.tcp._writer = self.writer

    async def asyncTearDown(self):
        await self.tcp.async_stop()
        self.timing.stop()

    async def event(self, device=None):
        device = device or self.device
        await self.tcp._state_listener(
            SimpleNamespace(
                data={"new_state": SimpleNamespace(entity_id=device.entity_id)}
            )
        )

    async def flush(self):
        await asyncio.gather(*list(self.tcp._publish_tasks.values()))

    async def test_identical_attribute_events_do_not_send_and_effective_changes_do(
        self,
    ):
        await self.tcp.async_publish_sync(self.device)
        for _ in range(1000):
            await self.event()
        await self.flush()
        self.assertEqual(len(self.writer.messages), 1)
        for temperature in range(20, 30):
            self.device.payload["t"] = temperature
            await self.event()
        await self.flush()
        self.assertEqual(len(self.writer.messages), 2)
        self.assertEqual(self.writer.messages[-1]["msg"]["t"], 29)

    async def test_concurrent_publishers_share_successful_content_cache(self):
        self.writer.gate = asyncio.Event()
        tasks = [
            asyncio.create_task(self.tcp.async_publish_sync(self.device))
            for _ in range(20)
        ]
        await self.writer.written.wait()
        self.writer.gate.set()
        await asyncio.gather(*tasks)
        self.assertEqual(len(self.writer.messages), 1)

    async def test_failed_send_is_retried_and_connection_reset_restores_state(self):
        self.writer.fail = True
        with self.assertLogs(CODE.tcp.LOGGER, level="WARNING"):
            await self.tcp.async_publish_sync(self.device)
        self.assertFalse(self.tcp._last_published)
        self.tcp._writer = Writer()
        await self.tcp.async_publish_sync(self.device)
        self.assertEqual(len(self.tcp._writer.messages), 1)
        await self.tcp._close_writer()
        self.tcp._writer = Writer()
        await self.tcp._publish_all()
        await self.tcp._publish_all()
        self.assertEqual(len(self.tcp._writer.messages), 1)

    async def test_controls_merge_feedback_without_merging_commands_or_old_values(self):
        other = Device(self.hass, "climate.other")
        self.tcp._topic_to_sync[other.topic] = other
        for temperature in range(20):
            self.tcp._handle_payload(
                {"topics": [self.device.topic], "msg": {"t": temperature}}
            )
        self.tcp._handle_payload({"topics": [other.topic], "msg": {"t": 12}})
        await self.event()
        self.assertEqual(len(self.tcp._publish_tasks), 2)
        self.assertEqual(len(self.device.controls), 20)
        await self.flush()
        self.assertEqual(len(self.writer.messages), 2)
        self.assertTrue(all(msg["msg"]["t"] == 26 for msg in self.writer.messages))
        await self.event()
        await self.flush()
        self.assertEqual(len(self.writer.messages), 2)
        self.assertEqual(
            self.device.generate_feedback_msg({"on": False, "t": 18}),
            self.device.payload,
        )

    async def test_continuous_changes_cannot_postpone_publishing_indefinitely(self):
        for temperature in range(40):
            self.device.payload["t"] = temperature
            await self.event()
            await asyncio.sleep(0.004)
        self.assertGreaterEqual(len(self.writer.messages), 2)
        await self.flush()
        self.assertEqual(self.writer.messages[-1]["msg"]["t"], 39)

    async def test_changes_during_writer_drain_are_eventually_sent(self):
        self.writer.gate = asyncio.Event()
        await self.event()
        await self.writer.written.wait()
        self.device.payload["t"] = 30
        await self.event()
        self.writer.gate.set()
        await self.flush()
        self.assertEqual([msg["msg"]["t"] for msg in self.writer.messages], [26, 30])

    async def test_remove_and_stop_cancel_pending_feedback_and_reject_late_events(self):
        self.tcp._handle_payload({"topics": [self.device.topic], "msg": {"t": 18}})
        await self.tcp.async_remove_sync(self.device.topic)
        self.tcp._writer = Writer()
        await asyncio.sleep(0.04)
        self.assertFalse(self.tcp._writer.messages)
        self.assertFalse(self.tcp._publish_tasks)
        other = Device(self.hass, "climate.other")
        self.tcp._topic_to_sync[other.topic] = other
        self.tcp._handle_payload({"topics": [other.topic], "msg": {"t": 20}})
        writer = self.tcp._writer
        await self.tcp.async_stop()
        await self.event(other)
        self.tcp._handle_payload({"topics": [other.topic], "msg": {"t": 21}})
        await self.tcp.async_add_sync(other)
        await asyncio.sleep(0.04)
        self.assertFalse(writer.messages)
        self.assertFalse(self.tcp._publish_tasks)
        self.assertFalse(self.tcp._topic_to_sync)
        self.assertEqual(len(other.controls), 1)

    async def test_stop_cancels_queued_control_sequence_before_later_operations(self):
        started = asyncio.Event()
        executed = []

        async def call(**kwargs):
            executed.append(kwargs["service"])
            started.set()
            await asyncio.Event().wait()

        self.hass.services = SimpleNamespace(async_call=call)
        self.device._async_call_services_sequential(
            [("climate", "first", {}), ("climate", "second", {})]
        )
        await started.wait()
        await self.tcp.async_stop()
        self.assertEqual(executed, ["first"])
        self.assertFalse(self.device._control_tasks)

    async def test_certificate_failure_retries_tls_without_plaintext_fallback(self):
        await self.tcp.async_stop()
        attempts = []
        retried = asyncio.Event()

        async def rejected(host, port, **kwargs):
            attempts.append((host, port, kwargs))
            if len(attempts) >= 2:
                retried.set()
            raise ssl.SSLCertVerificationError("simulated untrusted certificate")

        with (
            patch.object(CODE.tcp.asyncio, "open_connection", rejected),
            patch.object(CODE.tcp, "TCP_RECONNECT_DELAY", 0.01),
            self.assertLogs(CODE.tcp.LOGGER, level="WARNING"),
        ):
            try:
                await self.tcp.async_start()
                await asyncio.wait_for(retried.wait(), 1)
            finally:
                await self.tcp.async_stop()
        self.assertGreaterEqual(len(attempts), 2)
        for host, port, kwargs in attempts:
            self.assertEqual(host, "tcp-cn-03.bemfa.com")
            self.assertEqual(port, 8343)
            self.assertEqual(kwargs["server_hostname"], host)
            self.assertEqual(kwargs["ssl"].verify_mode, ssl.CERT_REQUIRED)
            self.assertEqual(kwargs["ssl"].minimum_version, ssl.TLSVersion.TLSv1_3)
        self.assertIsNone(self.tcp._writer)

    async def test_concurrent_start_creates_only_one_runner_and_listener(self):
        await self.tcp.async_stop()
        entered = asyncio.Event()
        resume = asyncio.Event()

        async def delayed_context(target, *args):
            entered.set()
            await resume.wait()
            return ssl.create_default_context()

        async def idle_runner():
            await asyncio.Event().wait()

        with (
            patch.object(self.hass, "async_add_executor_job", delayed_context),
            patch.object(self.tcp, "_run", idle_runner),
        ):
            first = asyncio.create_task(self.tcp.async_start())
            await entered.wait()
            second = asyncio.create_task(self.tcp.async_start())
            await asyncio.sleep(0)
            resume.set()
            try:
                await asyncio.gather(first, second)
                self.assertEqual(len(self.hass.bus.listeners), 1)
                self.assertEqual(sum(not task.done() for task in self.hass.tasks), 1)
            finally:
                # Also clean leaked tasks if this regression fails.
                await self.tcp.async_stop()
                for task in self.hass.tasks:
                    task.cancel()
                await asyncio.gather(*self.hass.tasks, return_exceptions=True)

    async def test_stop_during_certificate_loading_leaves_no_connection_task(self):
        await self.tcp.async_stop()
        entered = asyncio.Event()
        resume = asyncio.Event()

        async def delayed_context(target, *args):
            entered.set()
            await resume.wait()
            return ssl.create_default_context()

        async def idle_runner():
            await asyncio.Event().wait()

        with (
            patch.object(self.hass, "async_add_executor_job", delayed_context),
            patch.object(self.tcp, "_run", idle_runner),
        ):
            start = asyncio.create_task(self.tcp.async_start())
            await entered.wait()
            stop = asyncio.create_task(self.tcp.async_stop())
            await asyncio.sleep(0)
            resume.set()
            await asyncio.gather(start, stop)
            self.assertTrue(self.tcp._stopped.is_set())
            self.assertIsNone(self.tcp._runner)
            self.assertFalse(self.hass.bus.listeners)
            self.assertTrue(all(task.done() for task in self.hass.tasks))

    async def test_stalled_tls_handshake_times_out_and_stop_closes_retry(self):
        await self.tcp.async_stop()
        connected_peers = asyncio.Queue()
        closed_peers = asyncio.Queue()
        peers = []

        async def stalled(reader, writer):
            peers.append(writer)
            await connected_peers.put(writer)
            try:
                # Accept the TCP connection but never answer the TLS ClientHello.
                await reader.read()
            finally:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (TimeoutError, ConnectionError, ssl.SSLError):
                    pass
                await closed_peers.put(writer)

        server = await asyncio.start_server(stalled, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        original_open = asyncio.open_connection

        async def connect(host, port_number, **kwargs):
            return await original_open("127.0.0.1", port, **kwargs)

        try:
            with (
                patch.object(CODE.tcp.asyncio, "open_connection", connect),
                patch.object(CODE.tcp, "TCP_CONNECT_TIMEOUT", 0.1),
                patch.object(CODE.tcp, "TCP_RECONNECT_DELAY", 0.01),
                self.assertLogs(CODE.tcp.LOGGER, level="WARNING"),
            ):
                await self.tcp.async_start()
                first = await asyncio.wait_for(connected_peers.get(), 1)
                self.assertIs(await asyncio.wait_for(closed_peers.get(), 1), first)
                second = await asyncio.wait_for(connected_peers.get(), 1)
                await asyncio.wait_for(self.tcp.async_stop(), 1)
                self.assertIs(await asyncio.wait_for(closed_peers.get(), 1), second)
                self.assertIsNone(self.tcp._writer)
                self.assertIsNone(self.tcp._runner)
                self.assertFalse(self.hass.bus.listeners)
        finally:
            await self.tcp.async_stop()
            for writer in peers:
                writer.close()
            server.close()
            await server.wait_closed()

    async def test_reconnect_publishes_each_device_once_over_real_local_tls(self):
        if not self.has_openssl:
            self.skipTest("openssl is required to generate a local TLS certificate")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_3
        server_context.load_cert_chain(self.cert_path, self.key_path)
        create_context = ssl.create_default_context

        def trusted_context():
            return create_context(cafile=str(self.cert_path))

        received = asyncio.Queue()
        peers = []

        async def connected(reader, writer):
            peers.append(writer)
            try:
                while raw := await reader.readline():
                    await received.put(json.loads(raw))
            finally:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (TimeoutError, ConnectionError, ssl.SSLError):
                    pass

        server = await asyncio.start_server(
            connected,
            "127.0.0.1",
            0,
            ssl=server_context,
            ssl_shutdown_timeout=0.05,
        )
        original_open = asyncio.open_connection
        port = server.sockets[0].getsockname()[1]
        await self.tcp.async_stop()
        self.tcp = CODE.tcp.BemfaCloudTcp(self.hass, "offline")
        first = Device(self.hass)
        second = Device(self.hass, "climate.other")

        async def connect(host, port_number, **kwargs):
            self.assertEqual(host, "tcp-cn-03.bemfa.com")
            self.assertEqual(port_number, 8343)
            context = kwargs["ssl"]
            self.assertIsInstance(context, ssl.SSLContext)
            self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_3)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertEqual(kwargs["server_hostname"], host)
            reader, writer = await original_open("127.0.0.1", port, **kwargs)
            self.assertEqual(writer.get_extra_info("ssl_object").version(), "TLSv1.3")
            return reader, writer

        try:
            with (
                patch.object(CODE.tcp.asyncio, "open_connection", connect),
                patch.object(CODE.tcp.ssl, "create_default_context", trusted_context),
                patch.object(CODE.tcp, "TCP_RECONNECT_DELAY", 0.01),
                patch.object(CODE.tcp, "TCP_TLS_SHUTDOWN_TIMEOUT", 0.05),
            ):
                await self.tcp.async_start()
                await self.tcp.async_add_syncs([first, second])
                for _ in range(2):
                    messages = [
                        await asyncio.wait_for(received.get(), 1) for _ in range(3)
                    ]
                    states = [msg for msg in messages if msg["cmd"] == 2]
                    self.assertCountEqual(
                        [msg["topics"][0] for msg in states],
                        [first.topic, second.topic],
                    )
                    if len(peers) == 1:
                        peers[0].close()
                        await peers[0].wait_closed()
                self.assertEqual(len(peers), 2)
                # Simulate a server that stops processing TLS close_notify.
                peers[-1].transport.pause_reading()
                await asyncio.wait_for(self.tcp.async_stop(), 1)
                self.assertFalse(self.hass.bus.listeners)
                self.assertFalse(self.tcp._publish_tasks)
                self.assertTrue(all(task.done() for task in self.hass.tasks))
        finally:
            await self.tcp.async_stop()
            for writer in peers:
                writer.transport.abort()
            server.close()
            await server.wait_closed()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_setup_cleans_up_started_resources(self):
        hass = Hass()
        entered = asyncio.Event()
        original_start = CODE.service.BemfaCloudService.async_start

        async def suspended_start(service, config):
            await original_start(service, config)
            entered.set()
            await asyncio.Event().wait()

        async def idle_runner():
            await asyncio.Event().wait()

        entry = SimpleNamespace(entry_id="test", data={"uid": "offline"}, options={})
        with (
            patch.object(
                CODE.service.BemfaCloudService, "async_start", suspended_start
            ),
            patch.object(CODE.tcp.BemfaCloudTcp, "_run", lambda _: idle_runner()),
        ):
            task = asyncio.create_task(CODE.integration.async_setup_entry(hass, entry))
            await entered.wait()
            self.assertEqual(len(hass.bus.listeners), 4)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(hass.bus.listeners)
            self.assertTrue(all(task.done() for task in hass.tasks))
            self.assertNotIn(entry.entry_id, hass.data["bemfa_cloud"])

    async def test_partial_setup_failure_cleans_up_started_resources(self):
        hass = Hass()
        callbacks = []

        def at_started(hass, cb):
            callbacks.append(cb)
            return lambda: callbacks.remove(cb)

        original_listen = hass.bus.async_listen

        def failing_listen(name, listener):
            if name == "device_registry_updated":
                raise RuntimeError("simulated setup failure")
            return original_listen(name, listener)

        async def idle_runner():
            await asyncio.Event().wait()

        entry = SimpleNamespace(entry_id="test", data={"uid": "offline"}, options={})
        with (
            patch.object(CODE.service, "async_at_started", at_started),
            patch.object(CODE.tcp.BemfaCloudTcp, "_run", lambda _: idle_runner()),
            patch.object(hass.bus, "async_listen", failing_listen),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated setup failure"):
                await CODE.integration.async_setup_entry(hass, entry)
            self.assertFalse(hass.bus.listeners)
            self.assertFalse(callbacks)
            self.assertTrue(all(task.done() for task in hass.tasks))
            self.assertNotIn(entry.entry_id, hass.data["bemfa_cloud"])

    async def test_ten_config_updates_and_concurrent_reloads_leave_one_service(self):
        integration = CODE.integration
        hass = Hass()
        listeners = []
        unload_callbacks = []
        instances = []

        class Entry:
            entry_id = "test"
            data = {}
            options = {}

            def add_update_listener(self, listener):
                listeners.append(listener)
                return lambda: listeners.remove(listener)

            def async_on_unload(self, listener):
                unload_callbacks.append(listener)

        class Service:
            def __init__(self, *args):
                self.active = False
                instances.append(self)

            async def async_start(self, config):
                await asyncio.sleep(0)
                self.active = True

            async def async_stop(self):
                await asyncio.sleep(0)
                self.active = False

        entry = Entry()
        lock = asyncio.Lock()

        async def framework_reload(entry_id):
            self.assertEqual(entry_id, entry.entry_id)
            async with lock:
                await integration.async_unload_entry(hass, entry)
                for cb in unload_callbacks:
                    cb()
                unload_callbacks.clear()
                await integration.async_setup_entry(hass, entry)

        hass.config_entries = SimpleNamespace(async_reload=framework_reload)
        with (
            patch.object(integration, "BemfaCloudService", Service),
            patch.object(integration, "_async_update_next_step_notification"),
        ):
            await integration.async_setup_entry(hass, entry)
            for _ in range(10):
                await asyncio.gather(*(cb(hass, entry) for cb in tuple(listeners)))
                self.assertEqual(len(listeners), 1)
                self.assertEqual(sum(service.active for service in instances), 1)
            await asyncio.gather(
                *(integration.async_reload_entry(hass, entry) for _ in range(4))
            )
            self.assertEqual(len(listeners), 1)
            self.assertEqual(sum(service.active for service in instances), 1)
            await integration.async_unload_entry(hass, entry)
            self.assertFalse(any(service.active for service in instances))

    async def test_startup_callback_is_removed_and_active_restore_and_registry_work_cancelled(
        self,
    ):
        hass = Hass()
        callbacks = []
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        registry_entered = asyncio.Event()
        registry_cancelled = asyncio.Event()

        def at_started(hass, cb):
            callbacks.append(cb)
            return lambda: callbacks.remove(cb)

        async def blocked():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def idle_runner():
            await asyncio.Event().wait()

        async def blocked_registry(event):
            registry_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                registry_cancelled.set()

        with (
            patch.object(CODE.service, "async_at_started", at_started),
            patch.object(CODE.tcp.BemfaCloudTcp, "_run", lambda _: idle_runner()),
        ):
            service = CODE.service.BemfaCloudService(hass, {"uid": "offline"})
            service._async_restore_syncs_with_retry = blocked
            service._async_entity_registry_updated = blocked_registry
            await service.async_start({})
            await service.async_start({})
            self.assertEqual(len(callbacks), 1)
            self.assertEqual(len(hass.bus.listeners), 4)
            stale_callback = callbacks[0]
            await service.async_stop()
            self.assertFalse(callbacks)
            stale_callback(hass)
            self.assertFalse(service._tasks)
            self.assertFalse(hass.bus.listeners)
            self.assertFalse(entered.is_set())

            await service.async_start({})
            callbacks[0](hass)
            await entered.wait()
            registry_listener = next(
                cb
                for name, cb in hass.bus.listeners
                if name == "entity_registry_updated"
            )
            registry_listener(SimpleNamespace(data={}))
            await registry_entered.wait()
            self.assertEqual(len(service._tasks), 2)
            await service.async_stop()
            self.assertTrue(cancelled.is_set())
            self.assertTrue(registry_cancelled.is_set())
            self.assertFalse(service._tasks)
            self.assertTrue(all(task.done() for task in hass.tasks))


if __name__ == "__main__":
    unittest.main()
