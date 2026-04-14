"""Location Receiver integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components import webhook
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BOUND_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_GLOBAL_WEBHOOK_ID,
    CONF_GLOBAL_WEBHOOK_MANUAL,
    CONF_OSMAND_MODE,
    CONF_WEBHOOK_ID,
    DEVICE_TYPE_CSV,
    DEVICE_TYPE_OSMAND,
    DOMAIN,
    GLOBAL_STATE_KEY,
    GLOBAL_UNKNOWN_NOTIFICATIONS,
    GLOBAL_WEBHOOK_ID,
    GLOBAL_WEBHOOK_MANUAL,
    OSMAND_MAX_UNKNOWN_NOTIFICATIONS,
    OSMAND_MODE_GLOBAL,
    OSMAND_MODE_INDIVIDUAL,
    # OsmAnd query/form param names
    OSMAND_PARAM_DEVICE_ID,
    OSMAND_PARAM_DEVICE_ID_ALT,
    OSMAND_PARAM_HEADING,
    OSMAND_PARAM_HEADING_ALT,
    OSMAND_PARAM_LAT,
    OSMAND_PARAM_LON,
    OSMAND_PARAM_ALTITUDE,
    OSMAND_PARAM_SPEED,
    OSMAND_PARAM_ACCURACY,
    OSMAND_PARAM_BATTERY,
    OSMAND_PARAM_CHARGING,
    OSMAND_PARAM_TIMESTAMP,
    # OsmAnd JSON field names
    OSMAND_JSON_DEVICE_ID,
    OSMAND_JSON_LOCATION,
    OSMAND_JSON_TIMESTAMP,
    OSMAND_JSON_IS_MOVING,
    OSMAND_JSON_ODOMETER,
    OSMAND_JSON_EVENT,
    OSMAND_JSON_COORDS,
    OSMAND_JSON_LAT,
    OSMAND_JSON_LON,
    OSMAND_JSON_SPEED,
    OSMAND_JSON_HEADING,
    OSMAND_JSON_ALTITUDE,
    OSMAND_JSON_ACCURACY,
    OSMAND_JSON_BATTERY,
    OSMAND_JSON_BATTERY_LEVEL,
    OSMAND_JSON_IS_CHARGING,
    OSMAND_JSON_ACTIVITY,
    OSMAND_JSON_ACTIVITY_TYPE,
    # CarStatsViewer field names
    CSV_FIELD_ALT,
    CSV_FIELD_BATTERY_LEVEL,
    CSV_FIELD_BATTERY_POWER,
    CSV_FIELD_CHARGE_PORT,
    CSV_FIELD_DEVICE_ID,
    CSV_FIELD_GEAR,
    CSV_FIELD_HEADING,
    CSV_FIELD_IGNITION,
    CSV_FIELD_IS_CHARGING,
    CSV_FIELD_LAT,
    CSV_FIELD_LON,
    CSV_FIELD_SPEED,
    CSV_FIELD_TEMPERATURE,
    CSV_FIELD_TIMESTAMP,
    CSV_FIELD_ACCURACY,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]


# ──────────────────────────────────────────────────────────────────────
# Global state helpers
# ──────────────────────────────────────────────────────────────────────

def _global(hass: HomeAssistant) -> dict:
    """Return (and lazily create) the integration-wide global state dict."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(GLOBAL_STATE_KEY, {
        GLOBAL_WEBHOOK_ID: None,
        GLOBAL_WEBHOOK_MANUAL: False,
        GLOBAL_UNKNOWN_NOTIFICATIONS: 0,
    })


def get_global_webhook_id(hass: HomeAssistant) -> str | None:
    """Return the currently active OsmAnd global webhook ID, or None."""
    return _global(hass).get(GLOBAL_WEBHOOK_ID)


def _find_global_webhook_source(hass: HomeAssistant) -> tuple[str, bool] | None:
    """Scan all config entries to find a stored global webhook_id.

    Returns (webhook_id, is_manual) from the first global-mode entry that
    has CONF_GLOBAL_WEBHOOK_ID set, or None if none found.
    This is used to restore global state after HA restarts.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        wid = entry.data.get(CONF_GLOBAL_WEBHOOK_ID)
        if wid:
            return wid, entry.data.get(CONF_GLOBAL_WEBHOOK_MANUAL, False)
    return None


def _ensure_global_webhook_registered(hass: HomeAssistant) -> None:
    """Register the global OsmAnd webhook if not already registered.

    If the ID stored in global state differs from the registered one
    (e.g. after a reconfiguration), the old webhook is unregistered first.
    """
    g = _global(hass)
    webhook_id = g.get(GLOBAL_WEBHOOK_ID)
    if not webhook_id:
        return

    registered_id = g.get("_registered_webhook_id")

    # Already registered with the same ID — nothing to do
    if registered_id == webhook_id and g.get("_webhook_registered"):
        return

    # A different ID was previously registered — unregister it first
    if registered_id and registered_id != webhook_id:
        try:
            webhook.async_unregister(hass, registered_id)
            _LOGGER.info(
                "Location Receiver: global OsmAnd webhook changed — "
                "unregistered old ID (%s)", registered_id
            )
        except Exception:
            pass
        g["_webhook_registered"] = False
        g["_registered_webhook_id"] = None

    if g.get("_webhook_registered"):
        return

    _register_webhook_safe(
        hass,
        "OsmAnd Global",
        webhook_id,
        _osmand_global_webhook_handler,
    )
    g["_webhook_registered"] = True
    g["_registered_webhook_id"] = webhook_id
    _LOGGER.info(
        "Location Receiver: global OsmAnd webhook registered — URL: /api/webhook/%s",
        webhook_id,
    )


def _unregister_global_webhook_if_unused(hass: HomeAssistant) -> None:
    """Unregister the global webhook when no global-mode entries remain."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (
            entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_OSMAND
            and entry.data.get(CONF_OSMAND_MODE) == OSMAND_MODE_GLOBAL
        ):
            return  # still needed

    g = _global(hass)
    webhook_id = g.get(GLOBAL_WEBHOOK_ID)
    if webhook_id:
        try:
            webhook.async_unregister(hass, webhook_id)
            _LOGGER.info(
                "Location Receiver: global OsmAnd webhook unregistered (no more global devices)"
            )
        except Exception:
            pass
        g[GLOBAL_WEBHOOK_ID] = None
        g["_webhook_registered"] = False
        g["_registered_webhook_id"] = None
        g[GLOBAL_UNKNOWN_NOTIFICATIONS] = 0


# ──────────────────────────────────────────────────────────────────────
# HA lifecycle
# ──────────────────────────────────────────────────────────────────────

def _register_webhook_safe(
    hass: HomeAssistant,
    name: str,
    webhook_id: str,
    handler,
) -> None:
    """Register a webhook, unregistering any stale registration first.

    webhook.async_register raises ValueError if the ID is already taken.
    This can happen if a previous unload did not fully complete before the
    reload began.  We unregister silently and retry once.
    """
    try:
        webhook.async_register(hass, DOMAIN, name, webhook_id, handler)
    except ValueError:
        _LOGGER.debug(
            "Location Receiver: webhook %s already registered — unregistering stale "
            "registration and retrying",
            webhook_id,
        )
        try:
            webhook.async_unregister(hass, webhook_id)
        except Exception:
            pass
        webhook.async_register(hass, DOMAIN, name, webhook_id, handler)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Location Receiver component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Location Receiver from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    device_type = entry.data[CONF_DEVICE_TYPE]
    osmand_mode = entry.data.get(CONF_OSMAND_MODE)

    # ── Initialise per-entry runtime state ───────────────────────────
    hass.data[DOMAIN][entry.entry_id] = {
        "entry": entry,
        "latest_data": {},
    }

    # ── OsmAnd global mode ───────────────────────────────────────────
    if device_type == DEVICE_TYPE_OSMAND and osmand_mode == OSMAND_MODE_GLOBAL:
        g = _global(hass)

        # Always sync global state from entry data. This covers both:
        # - First setup: no global state yet, load from this entry
        # - After reconfiguration reload: entry data has new ID, update global state
        new_wid = entry.data.get(CONF_GLOBAL_WEBHOOK_ID)
        if new_wid and new_wid != g.get(GLOBAL_WEBHOOK_ID):
            g[GLOBAL_WEBHOOK_ID] = new_wid
            g[GLOBAL_WEBHOOK_MANUAL] = entry.data.get(CONF_GLOBAL_WEBHOOK_MANUAL, False)
        elif not g.get(GLOBAL_WEBHOOK_ID):
            source = _find_global_webhook_source(hass)
            if source:
                g[GLOBAL_WEBHOOK_ID], g[GLOBAL_WEBHOOK_MANUAL] = source

        _ensure_global_webhook_registered(hass)

        # Expose the active global webhook_id on the entry state for the UI
        hass.data[DOMAIN][entry.entry_id]["webhook_id"] = g.get(GLOBAL_WEBHOOK_ID)

    # ── OsmAnd individual mode ───────────────────────────────────────
    elif device_type == DEVICE_TYPE_OSMAND and osmand_mode == OSMAND_MODE_INDIVIDUAL:
        webhook_id = entry.data[CONF_WEBHOOK_ID]
        hass.data[DOMAIN][entry.entry_id]["webhook_id"] = webhook_id
        _register_webhook_safe(
            hass, entry.title, webhook_id,
            _make_osmand_individual_handler(entry.entry_id),
        )
        _LOGGER.info(
            "Location Receiver [%s]: OsmAnd individual webhook registered — "
            "URL: /api/webhook/%s",
            entry.title, webhook_id,
        )

    # ── CarStatsViewer ────────────────────────────────────────────────
    elif device_type == DEVICE_TYPE_CSV:
        webhook_id = entry.data[CONF_WEBHOOK_ID]
        hass.data[DOMAIN][entry.entry_id]["webhook_id"] = webhook_id
        _register_webhook_safe(
            hass, entry.title, webhook_id,
            _make_csv_handler(entry.entry_id),
        )
        _LOGGER.info(
            "Location Receiver [%s]: CarStatsViewer webhook registered — "
            "URL: /api/webhook/%s",
            entry.title, webhook_id,
        )

    else:
        _LOGGER.error(
            "Location Receiver [%s]: unknown device_type=%r osmand_mode=%r — "
            "no webhook registered",
            entry.title, device_type, osmand_mode,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    device_type = entry.data.get(CONF_DEVICE_TYPE)
    osmand_mode = entry.data.get(CONF_OSMAND_MODE)

    # Individual webhooks are owned by a single entry — unregister directly
    if not (device_type == DEVICE_TYPE_OSMAND and osmand_mode == OSMAND_MODE_GLOBAL):
        wid = entry.data.get(CONF_WEBHOOK_ID)
        if wid:
            webhook.async_unregister(hass, wid)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # After removing the entry, clean up the global webhook if no longer needed
    if device_type == DEVICE_TYPE_OSMAND and osmand_mode == OSMAND_MODE_GLOBAL:
        _unregister_global_webhook_if_unused(hass)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when data/options change."""
    await hass.config_entries.async_reload(entry.entry_id)


# ──────────────────────────────────────────────────────────────────────
# OsmAnd global webhook handler (shared by all global-mode entries)
# ──────────────────────────────────────────────────────────────────────

async def _osmand_global_webhook_handler(
    hass: HomeAssistant, webhook_id: str, request
) -> None:
    """Receive OsmAnd data on the shared global webhook and route by device_id."""
    _LOGGER.debug(
        "Location Receiver: global OsmAnd webhook received — method=%s content_type=%s",
        request.method,
        request.content_type,
    )
    data = await _read_osmand_request(request)
    if data is None:
        return

    _LOGGER.debug(
        "Location Receiver: global OsmAnd parsed — device_id=%s format=%s "
        "lat=%s lon=%s battery=%s",
        data.get("device_id"),
        data.get("osmand_format"),
        data.get("latitude"),
        data.get("longitude"),
        data.get("battery_level"),
    )
    incoming_device_id = data.get("device_id", "")

    # Find the entry whose bound_device_id matches
    target_entry_id: str | None = None
    for eid, state in hass.data[DOMAIN].items():
        if eid == GLOBAL_STATE_KEY:
            continue
        e: ConfigEntry = state.get("entry")
        if e is None:
            continue
        if (
            e.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_OSMAND
            and e.data.get(CONF_OSMAND_MODE) == OSMAND_MODE_GLOBAL
            and e.data.get(CONF_BOUND_DEVICE_ID) == incoming_device_id
        ):
            target_entry_id = eid
            break

    if target_entry_id is None:
        _handle_unknown_osmand_device(hass, incoming_device_id)
        return

    _dispatch(hass, target_entry_id, data)


def _handle_unknown_osmand_device(hass: HomeAssistant, device_id: str) -> None:
    """Notify about an unknown device_id received on the global webhook.

    After OSMAND_MAX_UNKNOWN_NOTIFICATIONS distinct unknown IDs the guard
    engages and further unknowns are silently dropped (logged at DEBUG only).
    """
    g = _global(hass)
    count = g.get(GLOBAL_UNKNOWN_NOTIFICATIONS, 0)

    if count < OSMAND_MAX_UNKNOWN_NOTIFICATIONS:
        g[GLOBAL_UNKNOWN_NOTIFICATIONS] = count + 1
        _LOGGER.warning(
            "Location Receiver: OsmAnd global webhook received unknown device_id '%s'. "
            "Add a new Location Receiver entry (OsmAnd → Global) bound to this ID.",
            device_id,
        )
        pn_create(
            hass,
            message=(
                f"Location Receiver received data from an unknown device: **{device_id}**.\n\n"
                f"To track it, add a new Location Receiver integration entry "
                f"(OsmAnd → Global webhook) and set the bound device ID to "
                f"`{device_id}`."
            ),
            title="Location Receiver: Unknown Device Detected",
            notification_id=f"{DOMAIN}_unknown_{device_id}",
        )
    else:
        _LOGGER.debug(
            "Location Receiver: ignoring unknown device_id '%s' (DoS guard active).",
            device_id,
        )


# ──────────────────────────────────────────────────────────────────────
# Individual handler factories
# ──────────────────────────────────────────────────────────────────────

def _make_osmand_individual_handler(entry_id: str):
    """OsmAnd handler for a dedicated per-device webhook."""

    async def handler(hass: HomeAssistant, webhook_id: str, request):
        _LOGGER.debug(
            "Location Receiver [%s]: OsmAnd individual webhook received — "
            "method=%s content_type=%s",
            entry_id,
            request.method,
            request.content_type,
        )
        data = await _read_osmand_request(request)
        if data is None:
            return
        _LOGGER.debug(
            "Location Receiver [%s]: OsmAnd individual parsed — device_id=%s "
            "format=%s lat=%s lon=%s battery=%s",
            entry_id,
            data.get("device_id"),
            data.get("osmand_format"),
            data.get("latitude"),
            data.get("longitude"),
            data.get("battery_level"),
        )
        _dispatch(hass, entry_id, data)

    return handler


def _make_csv_handler(entry_id: str):
    """CarStatsViewer JSON POST handler."""

    async def handler(hass: HomeAssistant, webhook_id: str, request):
        _LOGGER.debug(
            "Location Receiver [%s]: CarStatsViewer webhook received — "
            "method=%s content_type=%s",
            entry_id,
            request.method,
            request.content_type,
        )
        try:
            payload = await request.json()
        except Exception:
            _LOGGER.error("Location Receiver: failed to parse CarStatsViewer JSON payload")
            return
        _LOGGER.debug(
            "Location Receiver [%s]: CarStatsViewer parsed — vehicle_id=%s "
            "lat=%s lon=%s battery=%s",
            entry_id,
            payload.get("vehicle_id"),
            payload.get("lat"),
            payload.get("lon"),
            payload.get("battery_level"),
        )
        if not _validate_csv_payload(payload):
            return
        _dispatch(hass, entry_id, _parse_csv_payload(payload))

    return handler


# ──────────────────────────────────────────────────────────────────────
# Request parsers
# ──────────────────────────────────────────────────────────────────────

def _sf(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _sb(val) -> bool | None:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _speed_ms_to_kmh(speed_ms: float | None) -> float | None:
    """Convert speed from m/s to km/h, treating negative values as None.

    Some devices report -1 or other negative values when speed is unavailable
    or the GPS fix is poor. Negative speed is physically meaningless, so it
    is normalised to None rather than propagated to the entity.
    """
    if speed_ms is None or speed_ms < 0:
        return None
    return round(speed_ms * 3.6, 2)


def _parse_osmand_timestamp(raw) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str) and "T" in raw:
        return raw
    try:
        ts = float(raw)
        if ts > 1e10:
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return str(raw)


def _validate_osmand_params(params) -> bool:
    """Basic validation for OsmAnd query/form parameters.

    A payload is accepted if it has a device identifier AND at least one of:
    - valid latitude + longitude
    - a usable battery level value
    """
    device_id = params.get(OSMAND_PARAM_DEVICE_ID) or params.get(OSMAND_PARAM_DEVICE_ID_ALT)
    if not device_id:
        _LOGGER.warning(
            "Location Receiver: OsmAnd params rejected — missing device identifier (id/deviceid)"
        )
        return False

    has_position = (
        _sf(params.get(OSMAND_PARAM_LAT)) is not None
        and _sf(params.get(OSMAND_PARAM_LON)) is not None
    )
    has_battery = _sf(
        params.get(OSMAND_PARAM_BATTERY)
        or params.get("battery")
        or params.get("StateOfCharge")
    ) is not None

    if not has_position and not has_battery:
        _LOGGER.warning(
            "Location Receiver: OsmAnd params from '%s' rejected — "
            "payload must contain at least lat/lon or a battery level value",
            device_id,
        )
        return False

    return True


def _validate_osmand_json(payload: dict) -> bool:
    """Basic validation for OsmAnd JSON body.

    A payload is accepted if it has a device_id, a location object, AND at
    least one of: valid coords (lat+lon) or a battery level value.
    """
    if not isinstance(payload, dict):
        _LOGGER.warning("Location Receiver: OsmAnd JSON rejected — payload is not an object")
        return False

    device_id = payload.get(OSMAND_JSON_DEVICE_ID)
    if not device_id:
        _LOGGER.warning(
            "Location Receiver: OsmAnd JSON rejected — missing 'device_id' field"
        )
        return False

    loc = payload.get(OSMAND_JSON_LOCATION)
    if not isinstance(loc, dict):
        _LOGGER.warning(
            "Location Receiver: OsmAnd JSON from '%s' rejected — "
            "missing or invalid 'location' object",
            device_id,
        )
        return False

    coords = loc.get(OSMAND_JSON_COORDS) or {}
    battery = loc.get(OSMAND_JSON_BATTERY) or {}

    has_position = (
        isinstance(coords, dict)
        and _sf(coords.get(OSMAND_JSON_LAT)) is not None
        and _sf(coords.get(OSMAND_JSON_LON)) is not None
    )
    has_battery = (
        isinstance(battery, dict)
        and _sf(battery.get(OSMAND_JSON_BATTERY_LEVEL)) is not None
    )

    if not has_position and not has_battery:
        _LOGGER.warning(
            "Location Receiver: OsmAnd JSON from '%s' rejected — "
            "payload must contain at least lat/lon or a battery level value",
            device_id,
        )
        return False

    return True


def _validate_csv_payload(payload: dict) -> bool:
    """Basic validation for CarStatsViewer JSON payload.

    A payload is accepted if it is a JSON object AND contains at least one of:
    - valid latitude + longitude
    - a usable battery_level value
    """
    if not isinstance(payload, dict):
        _LOGGER.warning("Location Receiver: CarStatsViewer payload rejected — not a JSON object")
        return False

    has_position = (
        _sf(payload.get(CSV_FIELD_LAT)) is not None
        and _sf(payload.get(CSV_FIELD_LON)) is not None
    )
    has_battery = _sf(payload.get(CSV_FIELD_BATTERY_LEVEL)) is not None

    if not has_position and not has_battery:
        _LOGGER.warning(
            "Location Receiver: CarStatsViewer payload rejected — "
            "payload must contain at least lat/lon or battery_level"
        )
        return False

    return True


async def _read_osmand_request(request) -> dict | None:
    """Auto-detect OsmAnd JSON vs query/form, validate, and parse.

    Returns a normalised dict on success, or None if the payload is invalid
    and should be discarded.
    """
    content_type = request.content_type or ""

    if "application/json" in content_type or request.method == "POST":
        try:
            payload = await request.json()
            if isinstance(payload, dict) and OSMAND_JSON_LOCATION in payload:
                if not _validate_osmand_json(payload):
                    return None
                return _parse_osmand_json(payload)
        except Exception:
            pass

    params = request.query
    if not params.get(OSMAND_PARAM_DEVICE_ID) and not params.get(OSMAND_PARAM_DEVICE_ID_ALT):
        try:
            form = await request.post()
            if form:
                params = form
        except Exception:
            pass

    if not _validate_osmand_params(params):
        return None
    return _parse_osmand_params(params)


def _parse_osmand_json(payload: dict) -> dict:
    """Parse OsmAnd JSON body (Traccar Client >= 9.0)."""
    loc: dict = payload.get(OSMAND_JSON_LOCATION, {})
    coords: dict = loc.get(OSMAND_JSON_COORDS, {})
    battery: dict = loc.get(OSMAND_JSON_BATTERY, {})
    activity: dict = loc.get(OSMAND_JSON_ACTIVITY, {})

    speed_ms = _sf(coords.get(OSMAND_JSON_SPEED))
    batt_raw = _sf(battery.get(OSMAND_JSON_BATTERY_LEVEL))

    return {
        "device_id": payload.get(OSMAND_JSON_DEVICE_ID),
        "latitude": _sf(coords.get(OSMAND_JSON_LAT)),
        "longitude": _sf(coords.get(OSMAND_JSON_LON)),
        "altitude": _sf(coords.get(OSMAND_JSON_ALTITUDE)),
        "speed": _speed_ms_to_kmh(speed_ms),
        "accuracy": _sf(coords.get(OSMAND_JSON_ACCURACY)),
        "heading": _sf(coords.get(OSMAND_JSON_HEADING)),
        "battery_level": round(batt_raw * 100, 1) if batt_raw is not None else None,
        "is_charging": _sb(battery.get(OSMAND_JSON_IS_CHARGING)),
        "is_moving": _sb(loc.get(OSMAND_JSON_IS_MOVING)),
        "activity": activity.get(OSMAND_JSON_ACTIVITY_TYPE),
        "odometer": _sf(loc.get(OSMAND_JSON_ODOMETER)),
        "event": loc.get(OSMAND_JSON_EVENT),
        "received_at": _now_iso(),
        "device_timestamp": _parse_osmand_timestamp(loc.get(OSMAND_JSON_TIMESTAMP)),
        "osmand_format": "json",
    }


def _parse_osmand_params(params) -> dict:
    """Parse OsmAnd query-string or form parameters."""
    speed_ms = _sf(params.get(OSMAND_PARAM_SPEED))
    charging_raw = (params.get(OSMAND_PARAM_CHARGING) or "").lower()
    heading = _sf(params.get(OSMAND_PARAM_HEADING)) or _sf(params.get(OSMAND_PARAM_HEADING_ALT))
    device_id = params.get(OSMAND_PARAM_DEVICE_ID) or params.get(OSMAND_PARAM_DEVICE_ID_ALT)

    # Accept battery level under multiple param names: batt, battery, StateOfCharge
    battery_raw = (
        params.get(OSMAND_PARAM_BATTERY)      # "batt"
        or params.get("battery")
        or params.get("StateOfCharge")
    )

    return {
        "device_id": device_id,
        "latitude": _sf(params.get(OSMAND_PARAM_LAT)),
        "longitude": _sf(params.get(OSMAND_PARAM_LON)),
        "altitude": _sf(params.get(OSMAND_PARAM_ALTITUDE)),
        "speed": _speed_ms_to_kmh(speed_ms),
        "accuracy": _sf(params.get(OSMAND_PARAM_ACCURACY)),
        "heading": heading,
        "battery_level": _sf(battery_raw),
        "is_charging": charging_raw in ("true", "1", "yes"),
        "received_at": _now_iso(),
        "device_timestamp": _parse_osmand_timestamp(params.get(OSMAND_PARAM_TIMESTAMP)),
        "osmand_format": "params",
    }


def _parse_csv_payload(payload: dict) -> dict:
    """Parse CarStatsViewer JSON payload."""
    speed_ms = _sf(payload.get(CSV_FIELD_SPEED))
    return {
        "device_id": payload.get(CSV_FIELD_DEVICE_ID),
        "latitude": _sf(payload.get(CSV_FIELD_LAT)),
        "longitude": _sf(payload.get(CSV_FIELD_LON)),
        "altitude": _sf(payload.get(CSV_FIELD_ALT)),
        "speed": _speed_ms_to_kmh(speed_ms),
        "accuracy": _sf(payload.get(CSV_FIELD_ACCURACY)),
        "heading": _sf(payload.get(CSV_FIELD_HEADING)),
        "battery_level": _sf(payload.get(CSV_FIELD_BATTERY_LEVEL)),
        "is_charging": _sb(payload.get(CSV_FIELD_IS_CHARGING)),
        "charge_port_connected": _sb(payload.get(CSV_FIELD_CHARGE_PORT)),
        "ignition": payload.get(CSV_FIELD_IGNITION),
        "gear": payload.get(CSV_FIELD_GEAR),
        "power": _sf(payload.get(CSV_FIELD_BATTERY_POWER)),
        "temperature": _sf(payload.get(CSV_FIELD_TEMPERATURE)),
        "received_at": _now_iso(),
        "device_timestamp": payload.get(CSV_FIELD_TIMESTAMP),
    }


# ──────────────────────────────────────────────────────────────────────
# Dispatch
# ──────────────────────────────────────────────────────────────────────

def _dispatch(hass: HomeAssistant, entry_id: str, data: dict) -> None:
    """Store latest payload and fire dispatcher update signal."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    hass.data[DOMAIN][entry_id]["latest_data"] = data
    async_dispatcher_send(hass, f"{DOMAIN}_{entry_id}_update", data)
