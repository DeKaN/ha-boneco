"""Constants for the Boneco integration."""

from homeassistant.const import Platform
from pyboneco import BonecoDeviceClass, BonecoDeviceState

DOMAIN = "boneco"
MANUFACTURER = "Boneco"
WAIT_FOR_PAIRING_TIMEOUT = 30
WAIT_FOR_CONFIRM_PAIRING_TIMEOUT = 30
UPDATE_INTERVAL = 60
UPDATE_TIMEOUT = 30
AIR_FAN_SPEED_RANGE = (1, BonecoDeviceState.AIR_FAN_DEVICE_FAN_MAX_VALUE)
OTHER_FAN_SPEED_RANGE = (1, BonecoDeviceState.OTHER_DEVICE_FAN_MAX_VALUE)
DEVICES_WITH_FILTER = [BonecoDeviceClass.SIMPLE_CLIMATE, BonecoDeviceClass.TOP_CLIMATE]
DEVICE_CLEAN_PERIOD = 14
ISS_REPLACE_PERIOD = 365
FILTER_REPLACE_PERIOD = 365

PLATFORMS_BY_TYPE = {
    BonecoDeviceClass.FAN: [
        Platform.BINARY_SENSOR,
        Platform.FAN,
        Platform.SENSOR,
    ],
    BonecoDeviceClass.HUMIDIFIER: [
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.FAN,
        Platform.HUMIDIFIER,
        Platform.NUMBER,
        Platform.SENSOR,
        Platform.SWITCH,
    ],
    BonecoDeviceClass.SIMPLE_CLIMATE: [
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.FAN,
        Platform.HUMIDIFIER,
        Platform.NUMBER,
        Platform.SELECT,
        Platform.SENSOR,
        Platform.SWITCH,
    ],
    BonecoDeviceClass.TOP_CLIMATE: [
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.FAN,
        Platform.HUMIDIFIER,
        Platform.NUMBER,
        Platform.SELECT,
        Platform.SENSOR,
        Platform.SWITCH,
    ],
}
