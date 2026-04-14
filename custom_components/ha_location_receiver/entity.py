"""Base entity for Location Receiver."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import (
    CONF_DEVICE_TYPE,
    CONF_GLOBAL_WEBHOOK_ID,
    CONF_OSMAND_MODE,
    CONF_WEBHOOK_ID,
    DEVICE_TYPES,
    DOMAIN,
    OSMAND_MODE_GLOBAL,
)


def _get_active_webhook_id(entry: ConfigEntry) -> str | None:
    """Return the active webhook ID for an entry regardless of mode.

    Global-mode OsmAnd entries store their webhook under CONF_GLOBAL_WEBHOOK_ID;
    all other entries use CONF_WEBHOOK_ID.
    """
    if entry.data.get(CONF_OSMAND_MODE) == OSMAND_MODE_GLOBAL:
        return entry.data.get(CONF_GLOBAL_WEBHOOK_ID)
    return entry.data.get(CONF_WEBHOOK_ID)


class GpsTrackerBaseEntity(Entity):
    """Base class for Location Receiver entities."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the base entity."""
        self.hass = hass
        self._entry = entry
        self._entry_id = entry.entry_id
        self._device_name = entry.data[CONF_NAME]
        self._device_type = entry.data[CONF_DEVICE_TYPE]
        self._webhook_id = _get_active_webhook_id(entry)
        self._data: dict = {}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="Location Receiver",
            model=DEVICE_TYPES.get(self._device_type, self._device_type),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available (has received at least one update)."""
        return bool(self._data)

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        existing = self.hass.data[DOMAIN].get(self._entry_id, {}).get("latest_data", {})
        if existing:
            self._data = existing

        @callback
        def handle_update(data: dict) -> None:
            self._data = data
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self._entry_id}_update",
                handle_update,
            )
        )
