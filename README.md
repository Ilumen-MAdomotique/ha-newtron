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

A local-push Home Assistant integration for Newtron Smart boards (relay, dimmer, PRO and IR families). It talks to the boards over MQTT — no cloud dependency, no polling required for state changes.

## Supported boards

- **SMART 1R / 2R / 8R / 16R** — relay boards (switches, roller shutters, alarms)
- **SMART PRO** — relay board with data logger
- **SMART DIMMER** — 8× analog outputs (0-10 V)
- **SMART IR** — learned IR remotes (universal and air-conditioner profiles)

## Features

- Switches, binary sensor inputs, roller shutters, virtual thermostats
- Alarm system, timers, webhooks configuration — all exposed as entities and/or through the built-in configuration panel
- A dedicated sidebar panel (admin only) to configure inputs, outputs, shutters, alarms, timers, webhooks, IR remotes and network settings without leaving Home Assistant
- Automatic device discovery from your MQTT broker's retained messages

## Requirements

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
- **Enter the device topic manually** — for boards that didn't answer the search

## License

MIT — see [LICENSE](LICENSE).
