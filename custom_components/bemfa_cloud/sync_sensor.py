"""Support for bemfa service."""

import logging
from typing import Any

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS
from homeassistant.core import HomeAssistant
from .utils import has_key
from .const import (
    TopicSuffix,
)
from .sync import SYNC_TYPES, Sync, UNPUBLISHABLE_STATES

_LOGGING = logging.getLogger(__name__)

SENSOR_PAYLOAD_BY_DEVICE_CLASS = {
    SensorDeviceClass.TEMPERATURE: "t",
    SensorDeviceClass.HUMIDITY: "h",
    SensorDeviceClass.ILLUMINANCE: "illuminance",
    SensorDeviceClass.PM25: "pm25",
    SensorDeviceClass.CO2: "co2",
}
SENSOR_MSG_INDEX_BY_PAYLOAD = {
    "t": 1,
    "h": 2,
    "illuminance": 4,
    "pm25": 5,
    "co2": 6,
}


@SYNC_TYPES.register("sensor")
class Sensor(Sync):
    """Sync a hass sensor entity to bemfa sensor device."""

    @staticmethod
    def get_config_step_id() -> str:
        return "sync_config_sensor"

    @staticmethod
    def _get_topic_suffix() -> TopicSuffix:
        return TopicSuffix.SENSOR

    @classmethod
    def collect_supported_syncs(cls, hass: HomeAssistant):
        """Collect supported hass sensor entities."""
        return [
            cls(hass, state.entity_id, state.name)
            for state in hass.states.async_all(SENSOR_DOMAIN)
            if cls._payload_name(state.attributes) is not None
        ]

    def get_watched_entity_ids(self) -> list[str]:
        return [self._entity_id]

    def _generate_msg_parts(self) -> list[str]:
        state = self._hass.states.get(self._entity_id)
        if state is None or state.state in UNPUBLISHABLE_STATES:
            return []

        payload_name = self._payload_name(state.attributes)
        if payload_name is None:
            return []

        msg = [""] * SENSOR_MSG_INDEX_BY_PAYLOAD[payload_name]
        msg.append(state.state)
        return msg

    def _generate_msg_payload(self) -> dict[str, Any]:
        state = self._hass.states.get(self._entity_id)
        if state is None or state.state in UNPUBLISHABLE_STATES:
            return {}

        payload_name = self._payload_name(state.attributes)
        if payload_name is None:
            return {}

        try:
            value: Any = float(state.state)
            if value.is_integer():
                value = int(value)
        except ValueError:
            value = state.state
        return {payload_name: value}

    @staticmethod
    def _payload_name(attributes: dict[str, Any]) -> str | None:
        if not has_key(attributes, ATTR_DEVICE_CLASS):
            return None
        return SENSOR_PAYLOAD_BY_DEVICE_CLASS.get(attributes[ATTR_DEVICE_CLASS])
