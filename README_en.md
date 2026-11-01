# Bemfa Cloud Integration for Home Assistant

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![hacs][hacsbadge]][hacs]
[![Community Forum][forum-shield]][forum]

_Sync Home Assistant devices to Bemfa Cloud._

**Bemfa Cloud syncs Home Assistant devices to Bemfa Cloud, so you can control them through Bemfa Cloud, voice assistants, and other compatible apps.**

[简体中文](README.md) | English

## Features

- **Two authentication modes**: Bemfa private key input and OAuth login
- **Device selection**: Choose which HA devices to sync to Bemfa Cloud
- **Remote control**: Control synced devices through Bemfa Cloud, voice assistants, and other compatible apps
- **State sync**: Publishes HA state changes back to Bemfa Cloud
- **Name and room sync**: Mirrors HA entity name, area assignment, and area name changes to Bemfa device names and rooms
- **More stable sync relationships**: Renaming entities or changing rooms usually does not create duplicate Bemfa devices
- **BeHome compatibility**: Skips devices added by BeHome to avoid duplicate syncing

## Supported Device Types

Supported devices include outlets, lights, fans, sensors, air conditioners, switches, covers, thermostats, water heaters, TVs, and air purifiers. Scripts and automations can also be synced as switches. See the configuration page for the devices available to sync.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations**
3. Click **Explore & Download Repositories**
4. Search for **Bemfa Cloud**
5. Download it and restart Home Assistant

### HACS Custom Repository (Development/Test)

If Bemfa Cloud is not listed in the HACS store yet, or you want to test a development version:

1. Open the top-right HACS menu and choose **Custom repositories**
2. Enter repository URL: `https://github.com/bemfa/bemfa_cloud_ha`
3. Select **Integration** as the category
4. Search for **Bemfa Cloud**, download it, and restart Home Assistant

### Manual Installation

1. Open your Home Assistant configuration directory, where `configuration.yaml` is located
2. Create `custom_components` if it does not already exist
3. Copy `custom_components/bemfa_cloud` from this repository into HA's `custom_components` directory
4. Restart Home Assistant

## Configuration

To sync devices to multiple Bemfa Cloud accounts, add multiple Bemfa Cloud hubs. Each hub binds to one account and has its own entity sync configuration.

### Private Key Mode

1. Go to **Settings** -> **Devices & Services** -> **Add Integration**
2. Search for **Bemfa Cloud**
3. Choose **Private Key**
4. Enter your Bemfa private key `uid`
5. After login, select the HA entities to sync from the device list and save. The list shows devices such as air conditioners, covers, lights, and switches by default.
6. Click **Configure** on Bemfa Cloud to change the selection later. Existing syncs are selected by default. Deselect a device and save to stop syncing it; you can also deselect all devices.

### OAuth Mode

Private key mode is recommended for most users. To use OAuth login, create application credentials first.

1. Go to **Settings** -> **Devices & Services** -> **Helpers** -> **Application Credentials**
2. Create a new credential:
   - **Name**: `Bemfa Cloud`
   - **Domain**: `bemfa_cloud`
   - **Client ID**: `88ac425b4558463aa813aed1690db730`
   - **Client Secret**: any secure string
3. Add **Bemfa Cloud** from the integrations page
4. Choose OAuth and complete authorization

If OAuth redirects to `homeassistant.local` and the browser cannot open it, configure the correct Home Assistant URL in **Settings** -> **System** -> **Network**. For local Docker testing, use `http://localhost:8123`. For LAN access, use `http://your-ha-host-ip:8123`.

## Notes

- This integration syncs **HA -> Bemfa Cloud**
- BeHome syncs **Bemfa Cloud -> HA**
- Both integrations can be installed together. Bemfa Cloud skips BeHome-created entities to avoid duplicates and control loops
- The same HA entity can be added to different Bemfa Cloud hubs, and each hub keeps its own sync configuration
- Changing the entity ID of some HA entities may create a new device in Bemfa Cloud
- After upgrading from an older version, if duplicate devices appear in Bemfa Cloud, remove the old devices manually from the Bemfa console

## FAQ

### What should I do if sync fails?

Check that the Bemfa private key is correct, Home Assistant can access the internet, and the Home Assistant logs include Bemfa Cloud error details.

### Does removing a local sync delete the Bemfa Cloud device?

No. It only stops this integration from syncing that entity. The device in Bemfa Cloud must be removed manually from the Bemfa console.

### What happens if I change a Home Assistant entity ID?

Changing the ID of some entities may create a new device in Bemfa Cloud. If duplicates appear, remove the old device manually from the Bemfa console.

### How are air conditioner fan speeds matched?

Automatic fan speed, numbered speeds, and low, medium, and high speeds are matched to the modes supported by your HA air conditioner. Existing custom mappings are preserved.

## Support

- [GitHub Issues](https://github.com/bemfa/bemfa_cloud_ha/issues)
- [Home Assistant Community Forum](https://community.home-assistant.io/)

## Acknowledgements

Thanks to [larry-wong/bemfa](https://github.com/larry-wong/bemfa) for reference and inspiration.

## License

This project is licensed under the MIT License - see [LICENSE](LICENSE) for details.

---

[commits-shield]: https://img.shields.io/github/commit-activity/y/bemfa/bemfa_cloud_ha.svg?style=for-the-badge
[commits]: https://github.com/bemfa/bemfa_cloud_ha/commits/main
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/bemfa/bemfa_cloud_ha.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/bemfa/bemfa_cloud_ha.svg?style=for-the-badge
[releases]: https://github.com/bemfa/bemfa_cloud_ha/releases
