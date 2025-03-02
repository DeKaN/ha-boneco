# Boneco integration for Home Assistant

Integration to control [Boneco](https://www.boneco.com) devices via bluetooth.  
These devices require active connection for reading/writing data, so the integration connects every minute for reading data and then disconnects from your device. 

## Supported models
- H300/H300 CN
- H320/H320 CN
- H400/H400 CN
- H500/H500
- H600/H600
- H700/H700 US
- W400/W400 CN
- F225
- F235

## Known limitations
At this moment some features are not supported:
- purifier/hybrid mode (and switching modes also)
- timers
Additional reading data from device is required after each write due to device logic (it can update several fields after changing something).

## Installation

### Install from HACS (will be available later)

1. Have [HACS][hacs] installed, this will allow you to easily manage and track updates.
1. Search for "Boneco".
1. Click Install below the found integration.
1. In the HA UI go to Settings -> Integrations click "+" and search for "Boneco".

### Manual installation

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
1. If you do not have a `custom_components` directory (folder) there, you need to create it.
1. In the `custom_components` directory (folder) create a new folder called `boneco`.
1. Download file `boneco.zip` from the [latest release section][latest-release] in this repository.
1. Extract _all_ files from this archive you downloaded in the directory (folder) `boneco` you created.
1. Restart Home Assistant
1. In the HA UI go to Settings -> Integrations click "+" and search for "Boneco".
