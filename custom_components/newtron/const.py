"""Constants for the Newtron Smart integration."""

DOMAIN = "newtron"
MANUFACTURER = "Newtron"

CONF_TOPIC = "topic"
CONF_SERIAL = "serial_number"
CONF_MAC = "mac_address"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TOPIC_SCHEME = "topic_scheme"

# Legacy topic-scheme constants. Devices always use d/<topic>/<prefix>/<Cmd>
# (their full topic is d/%topic%/%prefix%/ whatever broker they point at), so
# the hub no longer branches on this — the values are kept only so existing
# config entries, which still carry the key, keep loading.
SCHEME_TASMOTA = "tasmota"  # plain Tasmota cmnd/<topic>/<Cmd> — never observed
SCHEME_CLOUD = "cloud"  # d/<topic>/<prefix>/<Cmd> — what every board uses
DEFAULT_TOPIC_SCHEME = SCHEME_CLOUD

DEFAULT_SCAN_INTERVAL = 30  # seconds, StatusAll polling
LAYOUT_REFRESH_EVERY = 4  # re-read RelayLayout (board names) every N polls
COMMAND_TIMEOUT = 8  # seconds to wait for a stat/ response
# DeviceStatus waits at setup before ConfigEntryNotReady. The first request is
# usually lost at startup, so a short first wait keeps HA from stalling 8 s.
DEVICE_STATUS_TIMEOUTS = (2, 4, 8)

# Board families (derived from DeviceStatus capabilities + StatusAll shape)
FAMILY_RELAY = "relay"      # SMART 1R / 2R / 8R / 16R
FAMILY_PRO = "pro"          # SMART PRO
FAMILY_DALI = "dali"        # SMART DALI (capabilities.dali is always true)
FAMILY_DIMMER = "dimmer"    # SMART DIMMER (8x AO 0-10V)
FAMILY_IR = "ir"            # SMART IR (learned IR remotes, doc §29)

PLATFORMS = [
    "binary_sensor",
    "button",
    "climate",
    "cover",
    "light",
    "scene",
    "select",
    "sensor",
    "switch",
]

SIGNAL_UPDATE = "newtron_update_{}"  # .format(entry_id)
DATA_INVENTORY = f"{DOMAIN}_inventory"  # hass.data key, see inventory.py

PANEL_URL_PATH = "newtron"
PANEL_STATIC_PATH = "/newtron-panel"
PANEL_TITLE = "Newtron"
PANEL_ICON = "mdi:home-lightning-bolt"

WS_DEVICES = "newtron/devices"
WS_COMMAND = "newtron/command"
WS_MODELS = "newtron/models"
WS_INVENTORY = "newtron/inventory"
WS_INVENTORY_LAYOUT = "newtron/inventory/layout"
WS_INVENTORY_SAVE = "newtron/inventory/save"
WS_INVENTORY_DELETE = "newtron/inventory/delete"
