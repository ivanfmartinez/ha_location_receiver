"""Device tracker platform for Location Receiver."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_DEVICE_TYPE, DEVICE_TYPES, DOMAIN
from .entity import _get_active_webhook_id

_LOGGER = logging.getLogger(__name__)

# Keys that have dedicated sensor / binary_sensor entities.
# Excluded from the generic extra-attributes dump to avoid duplication.
_SENSOR_ENTITY_KEYS = frozenset({
    "latitude",
    "longitude",
    "altitude",
    "speed",
    "battery_level",
    "is_charging",
    "charge_port_connected",
    "ignition",
    "gear",
    "power",
    "temperature",
})

# Payload keys already present as explicit named attributes — not repeated in extras.
_EXPLICIT_ATTR_KEYS = frozenset({
    "device_id",
    "heading",
    "accuracy",
    "received_at",
    "device_timestamp",
    # lat/lon are exposed by TrackerEntity.state_attributes natively
    "latitude",
    "longitude",
})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Location Receiver device tracker."""
    async_add_entities([LocationReceiverTracker(hass, entry)])


class LocationReceiverTracker(TrackerEntity, RestoreEntity):
    """GPS device tracker that receives location data via webhook.

    Follows the OwnTracks / Traccar pattern:
    - _attr_latitude / _attr_longitude / _attr_location_accuracy are set
      directly in the update callback so TrackerEntity's cached-property
      mechanism is satisfied correctly.
    - extra_state_attributes returns all required and extra payload fields.
    - RestoreEntity preserves the last known state across HA restarts.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Location"
    _attr_source_type = SourceType.GPS
    _attr_entity_category = None  # Explicit: primary entity, not diagnostic/config

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the tracker entity."""
        self.hass = hass
        self._entry = entry
        self._entry_id = entry.entry_id
        self._device_name = entry.data[CONF_NAME]
        self._device_type = entry.data[CONF_DEVICE_TYPE]
        self._webhook_id = _get_active_webhook_id(entry)

        # Payload data dict — updated on every webhook call
        self._payload: dict = {}

        # TrackerEntity cached attributes — set directly to satisfy HA cache
        self._attr_latitude: float | None = None
        self._attr_longitude: float | None = None
        self._attr_location_accuracy: float = 0

        self._attr_unique_id = f"{entry.entry_id}_location"

    # ── Device info ──────────────────────────────────────────────────

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="Location Receiver",
            model=DEVICE_TYPES.get(self._device_type, self._device_type),
        )

    # ── Availability ─────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """Available as soon as any payload has been received."""
        return bool(self._payload)

    # ── Attributes ───────────────────────────────────────────────────

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all tracker attributes required by the specification.

        Always included (once any data is received):
            device_id, heading, accuracy, webhook_received_at,
            device_timestamp, webhook_id

        Plus any payload field that has no dedicated sensor entity.
        """
        if not self._payload:
            return {}

        attrs: dict[str, Any] = {
            "device_id":           self._payload.get("device_id"),
            "heading":             self._payload.get("heading"),
            "accuracy":            self._payload.get("accuracy"),
            "webhook_received_at": self._payload.get("received_at"),
            "device_timestamp":    self._payload.get("device_timestamp"),
            "webhook_id":          self._webhook_id,
        }

        # Append extra fields that have no dedicated entity
        for key, value in self._payload.items():
            if key not in _SENSOR_ENTITY_KEYS and key not in _EXPLICIT_ATTR_KEYS:
                attrs[key] = value

        # Required keys are always returned (even as None) so the user sees them.
        # Extra keys are stripped if None to keep the UI clean.
        required = {
            "device_id", "heading", "accuracy",
            "webhook_received_at", "device_timestamp", "webhook_id",
        }
        return {k: v for k, v in attrs.items() if k in required or v is not None}

    # ── Restore state ────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Restore last known state and register dispatcher callback."""
        await super().async_added_to_hass()

        # Attempt to restore from HA recorder first
        restored = await self.async_get_last_state()

        # Then check in-memory runtime data (present if HA didn't restart)
        runtime_data = (
            self.hass.data
            .get(DOMAIN, {})
            .get(self._entry_id, {})
            .get("latest_data", {})
        )

        if runtime_data:
            self._apply_payload(runtime_data)
        elif restored and restored.attributes:
            # Restore position from recorder so map shows last known location
            try:
                lat = float(restored.attributes.get("latitude", 0) or 0)
                lon = float(restored.attributes.get("longitude", 0) or 0)
                acc = float(restored.attributes.get("gps_accuracy", 0) or 0)
                if lat or lon:
                    self._attr_latitude = lat
                    self._attr_longitude = lon
                    self._attr_location_accuracy = acc
                    # Reconstruct a minimal payload so available returns True
                    self._payload = {"_restored": True}
            except (TypeError, ValueError):
                pass

        @callback
        def handle_update(data: dict) -> None:
            self._apply_payload(data)
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self._entry_id}_update",
                handle_update,
            )
        )

    # ── Internal helpers ─────────────────────────────────────────────

    def _apply_payload(self, data: dict) -> None:
        """Store payload and update TrackerEntity cached attributes."""
        self._payload = data

        # Set _attr_* directly — this is what TrackerEntity's cached_properties
        # mechanism expects. Setting these triggers cache invalidation so HA
        # writes the correct lat/lon to the state machine on the next
        # async_write_ha_state() call.
        lat = data.get("latitude")
        lon = data.get("longitude")
        acc = data.get("accuracy")

        self._attr_latitude = float(lat) if lat is not None else None
        self._attr_longitude = float(lon) if lon is not None else None
        try:
            self._attr_location_accuracy = float(acc) if acc is not None else 0
        except (TypeError, ValueError):
            self._attr_location_accuracy = 0
