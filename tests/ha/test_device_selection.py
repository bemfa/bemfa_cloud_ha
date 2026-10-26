"""Device selection tests with real HA flows, registries, and sync discovery.

Run with HA installed: python -m unittest discover -s tests/ha -v
"""

import base64
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry

from custom_components.bemfa_cloud import _async_update_next_step_notification
from custom_components.bemfa_cloud.config_flow import ConfigFlow, OptionsFlowHandler
from custom_components.bemfa_cloud.service import BemfaCloudService


UID = "0123456789abcdef" * 2


class DeviceSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = tempfile.TemporaryDirectory()
        self.addCleanup(self.config.cleanup)
        self.hass = HomeAssistant(self.config.name)
        self.session = Mock()
        session_patch = patch(
            "custom_components.bemfa_cloud.http.async_get_clientsession",
            return_value=self.session,
        )
        session_patch.start()
        self.addCleanup(session_patch.stop)

        device_registry.async_setup(self.hass)
        await device_registry.async_load(self.hass)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        entry = self.entry({})
        self.hass.config_entries._entries[entry.entry_id] = entry
        device = device_registry.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("test", "living")}
        )
        registry = entity_registry.async_get(self.hass)
        await registry.async_load()
        for domain, name, platform, device_id in (
            ("light", "living", "test", device.id),
            ("climate", "living", "test", device.id),
            ("switch", "child", "test", device.id),
            ("switch", "wall", "test", None),
            ("sensor", "temperature", "test", None),
            ("binary_sensor", "door", "test", None),
            ("light", "cloud", "behome", None),
        ):
            registry.async_get_or_create(
                domain, platform, name, suggested_object_id=name, device_id=device_id
            )
            self.hass.states.async_set(
                f"{domain}.{name}", "on",
                {"friendly_name": name, "device_class": "temperature"},
            )
        self.syncs = {
            sync.entity_id: sync
            for sync in BemfaCloudService(self.hass, {"uid": UID}).collect_supported_syncs()
        }

    def entry(self, options):
        return ConfigEntry(
            version=1, minor_version=1, domain="bemfa_cloud", title="Test",
            data={"uid": UID}, options=options, source="user", unique_id="test",
            discovery_keys={}, subentries_data=[],
        )

    async def authenticated_flow(self, method):
        flow = ConfigFlow()
        flow.hass = self.hass
        with (
            patch.object(flow, "async_set_unique_id", new_callable=AsyncMock),
            patch.object(flow, "_abort_if_unique_id_configured"),
        ):
            if method == "keys":
                result = await flow.async_step_keys({"uid": UID})
            elif method == "oauth":
                result = await flow.async_oauth_create_entry({"token": {"uid": UID}})
            else:
                flow._wechat_login_data = {"uid": base64.b64encode(UID.encode()).decode()}
                result = await flow.async_step_wechat_done()
        return flow, result

    def choices(self, result):
        return next(iter(result["data_schema"].schema.values())).config["options"]

    async def test_all_login_methods_open_same_device_checklist(self):
        expected = {
            self.syncs[entity_id].topic
            for entity_id in ("light.living", "climate.living", "switch.wall")
        }
        for method in ("keys", "oauth", "wechat"):
            with self.subTest(method=method):
                flow, result = await self.authenticated_flow(method)
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["step_id"], "select_devices")
                self.assertEqual({item["value"] for item in self.choices(result)}, expected)
                self.assertEqual(result["data_schema"]({}), {"select": []})
                topic = self.syncs["light.living"].topic
                submitted = result["data_schema"]({"select": [topic]})
                result = await flow.async_step_select_devices(submitted)
                self.assertEqual(result["type"], "create_entry")
                self.assertEqual(result["data"]["uid"], UID)
                self.assertEqual(result["options"], {"config": {topic: {"name": "living"}}})
        self.session.post.assert_not_called()

    async def test_empty_device_list_can_finish_setup_without_syncs(self):
        for state in self.hass.states.async_all():
            self.hass.states.async_remove(state.entity_id)
        flow, result = await self.authenticated_flow("keys")
        self.assertEqual(self.choices(result), [])
        result = await flow.async_step_select_devices(result["data_schema"]({"select": []}))
        self.assertEqual(result["options"], {"config": {}})
        self.session.post.assert_not_called()

    async def test_legacy_dialogs_remain_available_without_normal_entry_points(self):
        login, result = await self.authenticated_flow("wechat")
        self.assertEqual(result["step_id"], "select_devices")
        legacy = await login.async_step_setup_next()
        self.assertEqual(legacy["step_id"], "setup_next")

        entry = self.entry({})
        flow = OptionsFlowHandler(entry)
        flow.hass = self.hass
        self.hass.data["bemfa_cloud"] = {
            entry.entry_id: {"service": BemfaCloudService(self.hass, {"uid": UID})}
        }
        normal = await flow.async_step_init()
        self.assertEqual(normal["type"], "form")
        self.assertEqual(normal["step_id"], "init")
        menu = await flow.async_step_legacy_menu()
        self.assertEqual(menu["type"], "menu")
        self.assertIn("create_sync", menu["menu_options"])
        legacy = await flow.async_step_create_sync()
        self.assertEqual(legacy["step_id"], "create_sync")
        details = await flow.async_step_create_sync({"select": "switch.wall"})
        self.assertEqual(details["type"], "form")
        result = await flow._async_step_sync_config_done({"name": "Wall"})
        self.assertEqual(result["data"]["config"], {
            self.syncs["switch.wall"].topic: {"name": "Wall"}
        })

    async def test_options_preselect_saved_devices_and_keep_custom_settings(self):
        climate_topic = self.syncs["climate.living"].topic
        light_topic = self.syncs["light.living"].topic
        sensor_topic = self.syncs["sensor.temperature"].topic
        settings = {"name": "My AC", "fan_speed_0_value": "auto", "fan_speed_7_value": "quiet"}
        original = {
            "config": {
                climate_topic: settings.copy(), light_topic: {"name": "Light"},
                sensor_topic: {"name": "Saved sensor"},
                "missingtopic001": {"name": "Temporarily missing"},
            },
            "other_option": "keep",
        }
        entry = self.entry(original)
        flow = OptionsFlowHandler(entry)
        flow.hass = self.hass
        result = await flow.async_step_init()
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "init")
        self.assertEqual(set(result["data_schema"]({})["select"]), set(original["config"]))
        choices = {item["value"] for item in self.choices(result)}
        self.assertIn(sensor_topic, choices)
        self.assertIn("missingtopic001", choices)
        self.assertNotIn(self.syncs["binary_sensor.door"].topic, choices)
        # Simply opening or abandoning the form must not change saved options.
        self.assertEqual(dict(entry.options), original)
        wall_topic = self.syncs["switch.wall"].topic
        selected = [climate_topic, wall_topic, "missingtopic001"]
        result = await flow.async_step_init(result["data_schema"]({"select": selected}))
        self.assertEqual(result["data"], {
            "config": {
                climate_topic: settings, wall_topic: {"name": "wall"},
                "missingtopic001": {"name": "Temporarily missing"},
            },
            "other_option": "keep",
        })
        self.assertEqual(dict(entry.options), original)
        self.session.post.assert_not_called()

    async def test_deselect_all_saves_empty_config_and_stays_unselected(self):
        topic = self.syncs["light.living"].topic
        flow = OptionsFlowHandler(self.entry({"config": {topic: {"name": "Light"}}}))
        flow.hass = self.hass
        result = await flow.async_step_init()
        result = await flow.async_step_init(result["data_schema"]({"select": []}))
        self.assertEqual(result["data"], {"config": {}})
        reopened = OptionsFlowHandler(self.entry(result["data"]))
        reopened.hass = self.hass
        form = await reopened.async_step_init()
        self.assertEqual(form["data_schema"]({})["select"], [])
        self.assertTrue(self.choices(form))
        with (
            patch("custom_components.bemfa_cloud.persistent_notification.async_create") as create,
            patch("custom_components.bemfa_cloud.persistent_notification.async_dismiss") as dismiss,
        ):
            _async_update_next_step_notification(self.hass, self.entry(result["data"]))
            create.assert_not_called()
            dismiss.assert_called_once()

    async def test_runtime_restores_only_the_saved_selection(self):
        old_topic = self.syncs["climate.living"].topic
        selected_topic = self.syncs["switch.wall"].topic
        flow = OptionsFlowHandler(self.entry({"config": {old_topic: {"name": "AC"}}}))
        flow.hass = self.hass
        form = await flow.async_step_init()
        result = await flow.async_step_init(form["data_schema"]({"select": [selected_topic]}))
        service = BemfaCloudService(self.hass, {"uid": UID})
        service._config = result["data"]["config"]
        with (
            patch.object(service._http, "async_create_topics", new_callable=AsyncMock, return_value={}) as create,
            patch.object(service._tcp, "async_add_syncs", new_callable=AsyncMock) as subscribe,
        ):
            await service._async_restore_syncs()
        self.assertEqual([item.topic for item in create.call_args.args[0]], [selected_topic])
        self.assertEqual([item.topic for item in subscribe.call_args.args[0]], [selected_topic])
        self.assertEqual(set(service._syncs_by_entity_id), {"switch.wall"})


if __name__ == "__main__":
    unittest.main()
