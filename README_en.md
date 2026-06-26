# Bemfa Cloud Integration for Home Assistant

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![hacs][hacsbadge]][hacs]
[![Community Forum][forum-shield]][forum]

_Sync Home Assistant devices to Bemfa Cloud._

**Bemfa Cloud maps local Home Assistant entities to Bemfa TCP V2 devices, so they can be controlled through Bemfa Cloud, voice assistants, and other Bemfa-compatible clients.**

[简体中文](README.md) | English

## Features

- **Two authentication modes**: Bemfa private key input and OAuth login
- **Automatic topic creation**: Creates Bemfa TCP V2 topics for supported HA entities
- **Batch creation**: Uses the batch API when multiple topics need to be created
- **TCP long connection**: Subscribes to control messages through Bemfa TCP JSON V2
- **State sync**: Publishes HA state changes back to Bemfa Cloud
- **Name and room sync**: Mirrors HA entity name, area assignment, and area name changes to Bemfa topic name and room
- **More stable sync relationships**: Renaming entities or changing rooms usually does not create duplicate Bemfa devices
- **Source filtering**: Skips BeHome-created HA entities to avoid syncing Bemfa devices back to Bemfa Cloud

## Supported Device Types

| Bemfa suffix | Device type | Default HA mapping |
| --- | --- | --- |
| `001` | Outlet | `switch` with `device_class=outlet` |
| `002` | Light | `light` |
| `003` | Fan | regular `fan` |
| `004` | Sensor | `sensor`, `binary_sensor` |
| `005` | Air conditioner | `climate` with cooling/heating support |
| `006` | Switch | `switch`, `input_boolean`, `script`, `automation`, `remote`, and other entities with `turn_on/turn_off` services |
| `009` | Cover | `cover` |
| `010` | Thermostat | non-air-conditioner `climate` |
| `011` | Water heater | `water_heater` |
| `012` | TV | `media_player` |
| `013` | Air purifier | `fan` with `device_class=air_purifier` |

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

### Private Key Mode

1. Go to **Settings** -> **Devices & Services** -> **Add Integration**
2. Search for **Bemfa Cloud**
3. Choose **Private Key**
4. Enter your Bemfa private key `uid`
5. Save. The integration will discover supported HA entities and create Bemfa topics automatically

### OAuth Mode

Most users should use private key mode. OAuth mode reuses the BeHome authentication flow and is mainly for scenarios that need the BeHome authorization flow. Users who use OAuth need to create application credentials first.

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
- The integration uses stable Home Assistant entity identifiers when possible. Entities without a stable identifier fall back to `entity_id`, so changing `entity_id` may create a new Bemfa topic
- After upgrading from an older version, if duplicate devices appear in Bemfa Cloud, remove the old devices manually from the Bemfa console

## FAQ

### What should I do if sync fails?

Check that the Bemfa private key is correct, Home Assistant can access the internet, and the Home Assistant logs include Bemfa Cloud error details.

### Does removing a local sync delete the Bemfa Cloud device?

No. It only stops this integration from syncing that entity. The Bemfa Cloud topic must be removed manually from the Bemfa console.

### What happens if I change a Home Assistant entity ID?

For entities without a stable identifier, changing `entity_id` may create a new device in Bemfa Cloud.

## Implementation Notes

1. The integration scans supported HA entities
2. It excludes entities created by BeHome and Bemfa Cloud itself
3. It generates Bemfa device topics from entity types
4. It creates topics through the NoSecret APIs with fixed `type=7` and `region=cn-03`
5. It subscribes to all topics through a TCP V2 long connection
6. It calls HA services when Bemfa control messages are received
7. It publishes HA state changes back to Bemfa Cloud

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
