<p align="center">
  <img src="logo.png" alt="Newtron" width="360">
</p>

<p align="center">
  Home Assistant integration for <a href="https://newtron.tech">Newtron Smart</a> relay, dimmer and IR boards, over MQTT.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom">
  <img src="https://github.com/Ilumen-MAdomotique/ha-newtron/actions/workflows/hacs.yml/badge.svg" alt="HACS Action">
  <img src="https://github.com/Ilumen-MAdomotique/ha-newtron/actions/workflows/hassfest.yml/badge.svg" alt="Hassfest">
</p>

---

## What this is

A local-push Home Assistant integration for Newtron Smart boards (relay, dimmer, PRO, IR and DALI families). It talks to the boards over MQTT — no cloud dependency, no polling required for state changes.

## Supported boards

- **SMART 1R / 2R / 8R / 16R** — relay boards (switches, roller shutters, alarms)
- **SMART PRO** — relay board with data logger
- **SMART DIMMER** — 8× analog outputs (0-10 V)
- **SMART IR** — learned IR remotes (universal and air-conditioner profiles)
- **SMART DALI** — up to 64 lights, 16 groups and 16 scenes

## Features

- Switches, binary sensor inputs, roller shutters, virtual thermostats
- Alarm system, timers, webhooks configuration — all exposed as entities and/or through the built-in configuration panel
- A dedicated sidebar panel (admin only) to configure inputs, outputs, shutters, alarms, timers, webhooks, IR remotes and network settings without leaving Home Assistant
- Boards found on the broker are offered automatically under "Discovered"
- Add a board by its serial number; its address is picked up on its own
- Configuration tab: prepare a board before it is installed (names, rooms, shutters, inputs, GPIO probes), then the board receives it when it connects
- Maintenance tab: firmware update (Newtron firmware only), city and country, hardware revision
- Outputs and shutters go to their rooms; a switch shown as a light stays in its room
- A board back from a restart or update is read again and its entities rebuilt
- English and French

## Screenshots

Demo boards, shown in French with the Newtron theme.

### Home Assistant

**A new board is offered under "Discovered"**

![A new board is offered under "Discovered"](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/discovered.jpg)

**All boards in one place**

![All boards in one place](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/boards.jpg)

**A board and its controls**

![A board and its controls](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/device.jpg)

**Everything sorted by room**

![Everything sorted by room](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/rooms.jpg)

### Newtron panel

**Live inputs and outputs, identity, network**

![Live inputs and outputs, identity, network](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/overview.jpg)

**Roller shutters**

![Roller shutters](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/shutters.jpg)

**Timers: fixed time, sunrise, sunset, prayer times**

![Timers: fixed time, sunrise, sunset, prayer times](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/timers.jpg)

**Firmware, location, hardware revision**

![Firmware, location, hardware revision](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/maintenance.jpg)

### Preparing an installation

**The boards of the site**

![The boards of the site](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/configuration.jpg)

**Prepare a board before it is installed**

![Prepare a board before it is installed](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/prepare-board.jpg)

**Name and room of each output**

![Name and room of each output](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/relays.jpg)

### Other boards

**SMART DIMMER**

![SMART DIMMER](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/dimmer.jpg)

**SMART DALI**

![SMART DALI](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/dali.jpg)

**SMART IR**

![SMART IR](https://raw.githubusercontent.com/Ilumen-MAdomotique/ha-newtron/master/docs/screenshots/ir.jpg)

## Requirements

- Home Assistant 2026.8 or newer
- The MQTT integration configured in Home Assistant, connected to a broker (e.g. the Mosquitto broker add-on)
- Each Newtron board configured, on its own web interface, to connect to **that same broker** (host, port, username, password)

## Installation

### Via HACS

1. HACS → Integrations → search for **Newtron Smart** (or add this repository as a custom repository if it isn't in the default list yet: `https://github.com/Ilumen-MAdomotique/ha-newtron`, category *Integration*)
2. Install, then restart Home Assistant

### Manual

1. Copy `custom_components/newtron` into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

Settings → Devices & services → Add integration → **Newtron Smart**

- **Search the MQTT broker for devices** — lists boards already announced on your broker
- **Enter the serial number manually** — for boards that didn't answer the search

## License

MIT — see [LICENSE](LICENSE).
