"""Constants for the Bemfa Cloud integration."""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Final

DOMAIN: Final = "bemfa_cloud"
NAME: Final = "Bemfa Cloud"
LOGGER = logging.getLogger(__package__)

EXCLUDED_SOURCE_PLATFORMS: Final = {DOMAIN, "behome"}

CONF_UID: Final = "uid"
CONF_REGION: Final = "region"
BEMFA_REGION: Final = "cn-03"
CONF_AUTH_MODE: Final = "auth_mode"
AUTH_MODE_KEYS: Final = "keys"
AUTH_MODE_OAUTH: Final = "oauth"
AUTH_MODE_WECHAT_SCAN: Final = "wechat_scan"

OAUTH_CLIENT_ID: Final = "88ac425b4558463aa813aed1690db730"
OAUTH_CLIENT_SECRET: Final = ""
OAUTH_AUTHORIZE_URL: Final = "https://cloud.bemfa.com/web/mi/index.html"
OAUTH_TOKEN_URL: Final = "https://pro.bemfa.com/vs/speaker/v1/v2SpeakerToken"
WECHAT_QR_URL: Final = "https://go.bemfa.com/v3/getwximg?key=bemfa&q=2"
WECHAT_QR_IMAGE_URL: Final = "https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket={ticket}"
WECHAT_LOGIN_POLL_URL: Final = "https://go.bemfa.com/vb/web/v2/wechatLoginByEventKey"

OPTIONS_CONFIG: Final = "config"
OPTIONS_SELECT: Final = "select"
OPTIONS_NAME: Final = "name"

OPTIONS_TEMPERATURE: Final = "temperature"
OPTIONS_HUMIDITY: Final = "humidity"
OPTIONS_ILLUMINANCE: Final = "illuminance"
OPTIONS_PM25: Final = "pm25"
OPTIONS_CO2: Final = "co2"

OPTIONS_FAN_SPEED_0_VALUE: Final = "fan_speed_0_value"
OPTIONS_FAN_SPEED_1_VALUE: Final = "fan_speed_1_value"
OPTIONS_FAN_SPEED_2_VALUE: Final = "fan_speed_2_value"
OPTIONS_FAN_SPEED_3_VALUE: Final = "fan_speed_3_value"
OPTIONS_FAN_SPEED_4_VALUE: Final = "fan_speed_4_value"
OPTIONS_FAN_SPEED_5_VALUE: Final = "fan_speed_5_value"
OPTIONS_FAN_SPEED_7_VALUE: Final = "fan_speed_7_value"
OPTIONS_FAN_SPEED_8_VALUE: Final = "fan_speed_8_value"
OPTIONS_FAN_SPEED_9_VALUE: Final = "fan_speed_9_value"

OPTIONS_SWING_OFF_VALUE: Final = "swing_off_value"
OPTIONS_SWING_HORIZONTAL_VALUE: Final = "swing_horizontal_value"
OPTIONS_SWING_VERTICAL_VALUE: Final = "swing_vertical_value"
OPTIONS_SWING_BOTH_VALUE: Final = "swing_both_value"


class TopicSuffix(StrEnum):
    """Bemfa topic suffixes."""

    OUTLET = "001"
    LIGHT = "002"
    FAN = "003"
    SENSOR = "004"
    CLIMATE = "005"
    SWITCH = "006"
    COVER = "009"
    THERMOSTAT = "010"
    WATER_HEATER = "011"
    TV = "012"
    AIR_PURIFIER = "013"


TCP_HOST: Final = "tcp-cn-03.bemfa.com"
TCP_PORT: Final = 8342
TCP_CONNECT_TIMEOUT: Final = 20
TCP_RECONNECT_DELAY: Final = 5
TCP_PING_INTERVAL: Final = 30
TCP_RESUBSCRIBE_INTERVAL: Final = 5 * 60

BEMFA_TOPIC_TYPE_TCP_V2: Final = 7
CREATE_TOPIC_URL: Final = "https://pro.bemfa.com/vs/web/v2/createTopicNoSecret"
ADD_TOPICS_URL: Final = "https://pro.bemfa.com/vs/web/v2/addTopicsNoSecret"
CHANGE_TOPIC_GROUP_URL: Final = "http://apis.bemfa.com/vb/api/v1/changeTopicGroup"
CHANGE_TOPIC_ROOM_URL: Final = "http://apis.bemfa.com/vb/api/v1/changeTopicRoom"
MODIFY_TOPIC_NAME_URL: Final = "https://apis.bemfa.com/va/modifyName"

TOPIC_PREFIX: Final = "ha"
MSG_SEPARATOR: Final = "#"
MSG_ON: Final = "on"
MSG_OFF: Final = "off"
MSG_PAUSE: Final = "pause"
MSG_SPEED_COUNT: Final = 4
