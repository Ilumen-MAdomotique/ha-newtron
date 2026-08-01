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
COMMAND_TIMEOUT = 8  # seconds to wait for a stat/ response

# Board families (derived from DeviceStatus capabilities + StatusAll shape)
FAMILY_RELAY = "relay"      # SMART 1R / 2R / 8R / 16R
FAMILY_PRO = "pro"          # SMART PRO
FAMILY_DIMMER = "dimmer"    # SMART DIMMER (8x AO 0-10V)
FAMILY_IR = "ir"            # SMART IR (learned IR remotes, doc §29)

PLATFORMS = [
    "binary_sensor",
    "button",
    "climate",
    "cover",
    "light",
    "select",
    "sensor",
    "switch",
]

SIGNAL_UPDATE = "newtron_update_{}"  # .format(entry_id)

PANEL_URL_PATH = "newtron"
PANEL_STATIC_PATH = "/newtron-panel"
PANEL_TITLE = "Newtron"
PANEL_ICON = "mdi:home-lightning-bolt"

WS_DEVICES = "newtron/devices"
WS_COMMAND = "newtron/command"
