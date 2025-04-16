# Boneco integration for Home Assistant
[![GitHub Release][releases-shield]][releases]

Integration to control [Boneco](https://www.boneco.com) devices via bluetooth.  
These devices require active connection for reading/writing data, so the integration connects every minute for reading data and then disconnects from your device. 

## Supported models
- H300/H300 CN
- H320/H320 CN
- H400/H400 CN
- H500
- H600
- H700/H700 US
- W400/W400 CN
- F225
- F235

## Known limitations
At this moment some features are not supported:
- timers
Additional reading data from device is required after each write due to device logic (it can update several fields after changing something).

## Installation

### Install from HACS

1. Have [HACS][hacs] installed, this will allow you to easily manage and track updates.
1. Search for "Boneco".
1. Click Install below the found integration.
1. In the HA UI go to Settings -> Integrations click "+" and search for "Boneco".

## Sample card
If you want to see when device has any problems you can add it to Lovelace like
```yaml
type: vertical-stack
cards:
  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.w400_has_error
        state: "off"
    card:
      type: humidifier
      entity: humidifier.w400
      features:
        - type: humidifier-toggle
        - style: icons
          type: humidifier-modes
      show_current_as_primary: true
  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.w400_has_error
        state_not: "off"
    card:
      type: entities
      entities:
        - entity: binary_sensor.w400_no_water
          secondary_info: last-changed
        - entity: binary_sensor.w400_drum_blocked
          secondary_info: last-changed
        - entity: binary_sensor.w400_fan_blocked
          secondary_info: last-changed
      state_color: true
      show_header_toggle: false
```
where `binary_sensor.w400_has_error` is a helper group for binary sensors, which include all binary sensors provided by integration

<!---->
[hacs]: https://github.com/hacs/integration
[releases-shield]: https://img.shields.io/github/v/release/DeKaN/ha-boneco?style=for-the-badge
[releases]: https://github.com/DeKaN/ha-boneco/releases
