"""The Bemfa Cloud integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components import persistent_notification

from .const import DOMAIN, OPTIONS_CONFIG
from .service import BemfaCloudService

from . import (  # noqa: F401
    sync_binary_sensor,
    sync_climate,
    sync_cover,
    sync_fan,
    sync_light,
    sync_sensor,
    sync_switch,
    sync_water_heater,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bemfa Cloud from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    service = BemfaCloudService(hass, dict(entry.data))
    await service.async_start(entry.options.get(OPTIONS_CONFIG, {}))
    hass.data[DOMAIN][entry.entry_id] = {"service": service}
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    _async_update_next_step_notification(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data is not None:
        await data["service"].async_stop()
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry."""

    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


def _async_update_next_step_notification(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remind users to configure syncs after adding an account."""

    notification_id = f"{DOMAIN}_{entry.entry_id}_next_step"
    if entry.options.get(OPTIONS_CONFIG):
        persistent_notification.async_dismiss(hass, notification_id)
        return

    if str(hass.config.language).lower().startswith("zh"):
        title = "Bemfa Cloud 已添加"
        message = (
            "账号已经添加成功。请回到 Bemfa Cloud 卡片，点击 **配置**，"
            "选择 **批量添加同步**，勾选要同步的设备。\n\n"
            "只有选中的实体才会创建到巴法云。"
        )
    else:
        title = "Bemfa Cloud added"
        message = (
            "The account has been added. Return to the Bemfa Cloud card, click "
            "**Configure**, and choose **Add syncs in bulk** to select the devices "
            "you want to sync.\n\n"
            "Only selected entities will be created in Bemfa Cloud."
        )

    persistent_notification.async_create(
        hass,
        message,
        title=title,
        notification_id=notification_id,
    )
