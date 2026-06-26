"""Support for bemfa service."""

import logging
from typing import Any
import voluptuous as vol

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from .utils import has_key
from .const import (
    OPTIONS_CO2,
    OPTIONS_HUMIDITY,
    OPTIONS_ILLUMINANCE,
    OPTIONS_PM25,
    OPTIONS_TEMPERATURE,
    TopicSuffix,
)
from .sync import SYNC_TYPES, Sync, UNPUBLISHABLE_STATES

_LOGGING = logging.getLogger(__name__)


def area_entities(hass: HomeAssistant, area_id: str) -> set[str]:
    """Return entity ids directly assigned to an area or to devices in that area."""

    entity_reg = entity_registry.async_get(hass)
    device_reg = device_registry.async_get(hass)
    entity_ids = {
        entry.entity_id
        for entry in entity_registry.async_entries_for_area(entity_reg, area_id)
    }
    for device in device_registry.async_entries_for_area(device_reg, area_id):
        entity_ids.update(
            entry.entity_id
            for entry in entity_registry.async_entries_for_device(entity_reg, device.id)
        )
    return entity_ids


@SYNC_TYPES.register("sensor")
class Sensor(Sync):
    """Sync a hass area to bemfa sensor device."""

    @staticmethod
    def get_config_step_id() -> str:
        return "sync_config_sensor"

    @staticmethod
    def _get_topic_suffix() -> TopicSuffix:
        return TopicSuffix.SENSOR

    @classmethod
    def collect_supported_syncs(cls, hass: HomeAssistant):
        """Group hass sensors by area. Each area maps a bemfa sensor device."""
        return [
            cls(hass, "area.{id}".format(id=area.id), area.name)
            for area in area_registry.async_get(hass).async_list_areas()
        ]

    def should_auto_create(self) -> bool:
        """Area sensor groups need explicit sensor selection before syncing."""

        return False

    def generate_details_schema(self) -> dict[str, Any]:
        temperature_sensors: dict[str, str] = {}
        humidity_sensors: dict[str, str] = {}
        illuminance_sensors: dict[str, str] = {}
        pm25_sensors: dict[str, str] = {}
        co2_sensors: dict[str, str] = {}

        # filter entities in our area
        try:
            area_id = self._entity_id.split(".", 1)[1]
        except IndexError:
            area_id = ""
        a_entities = area_entities(self._hass, area_id) if area_id else set()

        for state in self._hass.states.async_all(SENSOR_DOMAIN):
            if state.entity_id not in a_entities:
                continue
            if not has_key(state.attributes, ATTR_DEVICE_CLASS):
                continue
            for (_d, _c) in (
                (temperature_sensors, SensorDeviceClass.TEMPERATURE),
                (humidity_sensors, SensorDeviceClass.HUMIDITY),
                (illuminance_sensors, SensorDeviceClass.ILLUMINANCE),
                (pm25_sensors, SensorDeviceClass.PM25),
                (co2_sensors, SensorDeviceClass.CO2),
            ):
                if state.attributes[ATTR_DEVICE_CLASS] == _c:
                    _d[state.entity_id] = "{name} ({id})".format(
                        name=state.name, id=state.entity_id
                    )
                    break
        schema = super().generate_details_schema()
        for (_t, _d) in (
            (OPTIONS_TEMPERATURE, temperature_sensors),
            (OPTIONS_HUMIDITY, humidity_sensors),
            (OPTIONS_ILLUMINANCE, illuminance_sensors),
            (OPTIONS_PM25, pm25_sensors),
            (OPTIONS_CO2, co2_sensors),
        ):
            if _d:
                schema[
                    vol.Optional(
                        _t,
                        description={
                            "suggested_value": self._config[_t]
                            if _t in self._config and self._config[_t] in _d
                            else list(_d.keys())[0]
                        },
                    )
                ] = SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=value,
                                label=label,
                            )
                            for (value, label) in _d.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
        return schema

    def get_watched_entity_ids(self) -> list[str]:
        ids: list[str] = []
        for name in (
            OPTIONS_TEMPERATURE,
            OPTIONS_HUMIDITY,
            OPTIONS_ILLUMINANCE,
            OPTIONS_PM25,
            OPTIONS_CO2,
        ):
            if name in self._config:
                ids.append(self._config[name])
        return ids

    def _generate_msg_parts(self) -> list[str]:
        msg: list[str] = [""]
        for name in (
            OPTIONS_TEMPERATURE,
            OPTIONS_HUMIDITY,
            "",
            OPTIONS_ILLUMINANCE,
            OPTIONS_PM25,
            OPTIONS_CO2,
        ):
            if name in self._config:
                state = self._hass.states.get(self._config[name])
                if state is not None:
                    msg.append(state.state)
        return msg

    def _generate_msg_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for option_name, payload_name in (
            (OPTIONS_TEMPERATURE, "t"),
            (OPTIONS_HUMIDITY, "h"),
            (OPTIONS_PM25, "pm25"),
            (OPTIONS_CO2, "co2"),
            (OPTIONS_ILLUMINANCE, "illuminance"),
        ):
            if option_name not in self._config:
                continue
            state = self._hass.states.get(self._config[option_name])
            if state is None or state.state in UNPUBLISHABLE_STATES:
                continue
            try:
                value: Any = float(state.state)
                if value.is_integer():
                    value = int(value)
            except ValueError:
                value = state.state
            payload[payload_name] = value
        return payload
