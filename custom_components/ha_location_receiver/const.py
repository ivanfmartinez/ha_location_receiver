"""Constants for the Location Receiver integration."""

DOMAIN = "ha_location_receiver"
VERSION = "0.1.0"

# Config entry keys (per-device)
CONF_DEVICE_TYPE = "device_type"
CONF_WEBHOOK_ID = "webhook_id"
CONF_WEBHOOK_MANUAL = "webhook_manual"
CONF_CUSTOM_WEBHOOK_ID = "custom_webhook_id"
CONF_DEVICE_ID = "device_id"
CONF_OSMAND_MODE = "osmand_mode"          # "global" | "individual"
CONF_BOUND_DEVICE_ID = "bound_device_id"  # OsmAnd global mode: device_id sent by tracker

# Key used in hass.data[DOMAIN] to store integration-wide global state
# (not a per-entry key — lives at hass.data[DOMAIN]["global"])
GLOBAL_STATE_KEY = "global"

# Sub-keys inside hass.data[DOMAIN][GLOBAL_STATE_KEY]
GLOBAL_WEBHOOK_ID = "osmand_global_webhook_id"
GLOBAL_WEBHOOK_MANUAL = "osmand_global_webhook_manual"
GLOBAL_UNKNOWN_NOTIFICATIONS = "osmand_unknown_notifications"

# hass.data storage key that persists the global webhook_id across restarts
# We store it in the first global-mode entry's data under this key so it
# survives reloads without needing a separate config store.
CONF_GLOBAL_WEBHOOK_ID = "global_webhook_id"
CONF_GLOBAL_WEBHOOK_MANUAL = "global_webhook_manual"

WEBHOOK_ID_MIN_LENGTH = 8

# OsmAnd webhook modes
OSMAND_MODE_GLOBAL = "global"
OSMAND_MODE_INDIVIDUAL = "individual"

OSMAND_MODES = {
    OSMAND_MODE_GLOBAL: "Global webhook (identify by device_id)",
    OSMAND_MODE_INDIVIDUAL: "Individual webhook (one per device)",
}

# Maximum unknown device_id notifications before ignoring (DoS guard)
OSMAND_MAX_UNKNOWN_NOTIFICATIONS = 2

DEVICE_TYPE_OSMAND = "osmand"
DEVICE_TYPE_CSV = "carstatsviewer"

DEVICE_TYPES = {
    DEVICE_TYPE_OSMAND: "OsmAnd / Traccar",
    DEVICE_TYPE_CSV: "CarStatsViewer (CSV)",
}

# Entity keys - shared
ENTITY_BATTERY_LEVEL = "battery_level"
ENTITY_SPEED = "speed"
ENTITY_ALTITUDE = "altitude"
ENTITY_IS_CHARGING = "is_charging"
ENTITY_DEVICE_TRACKER = "location"
ENTITY_HEADING = "heading"
# Composed to build device tracker
ENTITY_LATITUDE = "latitude"
ENTITY_LONGITUDE = "longitude"
ENTITY_ACCURACY = "accuracy"

# Entity keys - OsmAnd only
ENTITY_IS_MOVING = "is_moving"
ENTITY_ODOMETER = "odometer"
ENTITY_EVENT = "event"
ENTITY_ACTIVITY = "activity"

# Entity keys - CSV only
ENTITY_TEMPERATURE = "temperature"
ENTITY_CHARGE_PORT_CONNECTED = "charge_port_connected"
ENTITY_IGNITION = "ignition"
ENTITY_POWER = "power"
ENTITY_GEAR = "gear"

# Attribute keys (will not became entities)
ATTR_DEVICE_ID = "device_id"
ATTR_DEVICE_TIMESTAMP = "device_timestamp"
ATTR_WEBHOOK_RECEIVED_AT = "webhook_received_at"
ATTR_OSMAND_FORMAT = "osmand_format"  # "json" or "params"
ATTR_WEBHOOK_ID = "webhook_id"

# OsmAnd query / form parameter names (GET or POST form-encoded)
OSMAND_PARAM_DEVICE_ID = "id"
OSMAND_PARAM_DEVICE_ID_ALT = "deviceid"   # alternate key accepted by Traccar
OSMAND_PARAM_TIMESTAMP = "timestamp"
OSMAND_PARAM_LAT = "lat"
OSMAND_PARAM_LON = "lon"
OSMAND_PARAM_SPEED = "speed"
OSMAND_PARAM_ALTITUDE = "altitude"
OSMAND_PARAM_ACCURACY = "accuracy"
OSMAND_PARAM_HEADING = "bearing"
OSMAND_PARAM_HEADING_ALT = "heading"      # alternate key used by some clients
OSMAND_PARAM_BATTERY = "batt"
OSMAND_PARAM_CHARGING = "charge"

# OsmAnd JSON format field names (POST application/json — Traccar Client >= 9.0)
OSMAND_JSON_DEVICE_ID = "device_id"
OSMAND_JSON_LOCATION = "location"
OSMAND_JSON_TIMESTAMP = "timestamp"
OSMAND_JSON_IS_MOVING = "is_moving"
OSMAND_JSON_ODOMETER = "odometer"
OSMAND_JSON_EVENT = "event"
OSMAND_JSON_COORDS = "coords"
OSMAND_JSON_LAT = "latitude"
OSMAND_JSON_LON = "longitude"
OSMAND_JSON_SPEED = "speed"
OSMAND_JSON_HEADING = "heading"
OSMAND_JSON_ALTITUDE = "altitude"
OSMAND_JSON_ACCURACY = "accuracy"
OSMAND_JSON_BATTERY = "battery"
OSMAND_JSON_BATTERY_LEVEL = "level"       # decimal 0-1; converted to % on parse
OSMAND_JSON_IS_CHARGING = "is_charging"
OSMAND_JSON_ACTIVITY = "activity"
OSMAND_JSON_ACTIVITY_TYPE = "type"

# CarStatsViewer JSON field names
CSV_FIELD_TIMESTAMP = "timestamp"
CSV_FIELD_LAT = "lat"
CSV_FIELD_LON = "lon"
CSV_FIELD_ALT = "alt"
CSV_FIELD_SPEED = "speed"
CSV_FIELD_HEADING = "heading"
CSV_FIELD_ACCURACY = "accuracy"
CSV_FIELD_BATTERY_LEVEL = "batteryLevel"
CSV_FIELD_POWER_mW = "power"
CSV_FIELD_CHARGE_PORT = "chargePortConnected"
CSV_FIELD_IGNITION = "ignitionState"
CSV_FIELD_GEAR = "selectedGear"
CSV_FIELD_STATE_OF_CHARGE = "stateOfCharge"
CSV_FIELD_TEMPERATURE = "ambientTemperature"
