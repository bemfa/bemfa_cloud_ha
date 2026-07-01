"""Support for bemfa service."""
from __future__ import annotations

from collections.abc import Mapping, Callable
from typing import Any
from homeassistant.components.fan import (
    ATTR_OSCILLATING,
    ATTR_PERCENTAGE,
    ATTR_PERCENTAGE_STEP,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    DOMAIN,
    SERVICE_OSCILLATE,
    SERVICE_SET_PERCENTAGE,
)
from homeassistant.const import ATTR_DEVICE_CLASS, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.util.read_only_dict import ReadOnlyDict
from .const import MSG_OFF, MSG_ON, TopicSuffix
from .utils import has_key
from .sync import SYNC_TYPES, ControllableSync, UNPUBLISHABLE_STATES


@SYNC_TYPES.register("fan")
class Fan(ControllableSync):
    """Sync a hass fan entity to bemfa fan device."""

    @staticmethod
    def get_config_step_id() -> str:
        return "sync_config_fan"

    @staticmethod
    def _get_topic_suffix() -> TopicSuffix:
        return TopicSuffix.FAN

    @staticmethod
    def _supported_domain() -> str:
        return DOMAIN

    @classmethod
    def collect_supported_syncs(cls, hass):
        return [
            cls(hass, state.entity_id, state.name)
            for state in hass.states.async_all(cls._supported_domain())
            if state.attributes.get(ATTR_DEVICE_CLASS) != "air_purifier"
        ]

    def _msg_generators(
        self,
    ) -> list[Callable[[str, ReadOnlyDict[Mapping[str, Any]]], str | int]]:
        return [
            lambda state, attributes: MSG_ON if state == STATE_ON else MSG_OFF,
            lambda state, attributes: self._fan_speed_value(attributes) or "",
            lambda state, attributes: 1
            if has_key(attributes, ATTR_OSCILLATING) and attributes[ATTR_OSCILLATING]
            else 0
            if has_key(attributes, ATTR_OSCILLATING)
            else "",
        ]

    def _generate_msg_payload(self) -> dict[str, Any]:
        """Generate a Bemfa fan JSON message, keeping speed when turned off."""
        state = self._hass.states.get(self._entity_id)
        if state is None or state.state in UNPUBLISHABLE_STATES:
            return {}

        payload: dict[str, Any] = {"on": state.state == STATE_ON}
        if speed := self._fan_speed_value(state.attributes):
            payload["v"] = speed
        return payload

    @staticmethod
    def _fan_speed_value(attributes: ReadOnlyDict[Mapping[str, Any]]) -> int | None:
        """Return Bemfa fan speed value in the supported 1-5 range."""
        if not has_key(attributes, ATTR_PERCENTAGE) or not has_key(
            attributes, ATTR_PERCENTAGE_STEP
        ):
            return None

        percentage_step = attributes[ATTR_PERCENTAGE_STEP]
        if not percentage_step:
            return None

        return min(max(round(attributes[ATTR_PERCENTAGE] / percentage_step), 1), 5)

    def _msg_resolvers(
        self,
    ) -> list[
        (
            int,
            int,
            Callable[
                [list[str | int], ReadOnlyDict[Mapping[str, Any]]],
                (str, str, dict[str, Any]),
            ],
        )
    ]:
        return [
            (
                0,
                2,
                lambda msg, attributes: (
                    DOMAIN,
                    SERVICE_SET_PERCENTAGE,
                    {
                        ATTR_PERCENTAGE: min(
                            max(msg[1], 1) * attributes[ATTR_PERCENTAGE_STEP], 100
                        )
                    },
                )
                if len(msg) > 1 and has_key(attributes, ATTR_PERCENTAGE_STEP)
                else (
                    DOMAIN,
                    SERVICE_TURN_ON if msg[0] == MSG_ON else SERVICE_TURN_OFF,
                    {},
                ),
            ),
            (
                2,
                3,
                lambda msg, attributes: (
                    DOMAIN,
                    SERVICE_OSCILLATE,
                    {ATTR_OSCILLATING: msg[0] == 1},
                ),
            ),
        ]


@SYNC_TYPES.register("air_purifier")
class AirPurifier(Fan):
    """Sync a fan-mode air purifier to Bemfa air purifier device."""

    @staticmethod
    def get_config_step_id() -> str:
        return "sync_config_air_purifier"

    @staticmethod
    def _get_topic_suffix() -> TopicSuffix:
        return TopicSuffix.AIR_PURIFIER

    @classmethod
    def collect_supported_syncs(cls, hass):
        return [
            cls(hass, state.entity_id, state.name)
            for state in hass.states.async_all(cls._supported_domain())
            if state.attributes.get(ATTR_DEVICE_CLASS) == "air_purifier"
        ]

    def _msg_generators(
        self,
    ) -> list[Callable[[str, ReadOnlyDict[Mapping[str, Any]]], str | int]]:
        return [
            lambda state, attributes: MSG_ON if state == STATE_ON else MSG_OFF,
            lambda state, attributes: attributes[ATTR_PRESET_MODE]
            if has_key(attributes, ATTR_PRESET_MODE)
            else "",
        ]

    def _generate_msg_payload(self) -> dict[str, Any]:
        """Generate a Bemfa air purifier JSON message."""
        state = self._hass.states.get(self._entity_id)
        if state is None or state.state in UNPUBLISHABLE_STATES:
            return {}

        payload: dict[str, Any] = {"on": state.state == STATE_ON}
        if has_key(state.attributes, ATTR_PRESET_MODE):
            payload["mode"] = state.attributes[ATTR_PRESET_MODE]
        return payload

    def _msg_resolvers(
        self,
    ) -> list[
        (
            int,
            int,
            Callable[
                [list[str | int], ReadOnlyDict[Mapping[str, Any]]],
                (str, str, dict[str, Any]),
            ],
        )
    ]:
        return [
            *super()._msg_resolvers(),
        ]
