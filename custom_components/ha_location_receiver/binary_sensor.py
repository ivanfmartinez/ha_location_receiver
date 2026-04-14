"""Binary sensor platform for Location Receiver."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_TYPE_CSV
from .entity import GpsTrackerBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass
class GpsTrackerBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe a Location Receiver binary sensor."""
    data_key: str = ""


SHARED_BINARY_SENSORS: tuple[GpsTrackerBinarySensorEntityDescription, ...] = (
    GpsTrackerBinarySensorEntityDescription(
        key="is_charging",
        data_key="is_charging",
        name="Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        icon="mdi:battery-charging",
        entity_registry_enabled_default=False,
    ),
)

CSV_BINARY_SENSORS: tuple[GpsTrackerBinarySensorEntityDescription, ...] = (
    GpsTrackerBinarySensorEntityDescription(
        key="charge_port_connected",
        data_key="charge_port_connected",
        name="Charge Port Connected",
        device_class=BinarySensorDeviceClass.PLUG,
        icon="mdi:ev-plug-type2",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Location Receiver binary sensors."""
    device_type = entry.data.get("device_type")
    entities: list[GpsTrackerBinarySensor] = []

    for description in SHARED_BINARY_SENSORS:
        entities.append(GpsTrackerBinarySensor(hass, entry, description))

    if device_type == DEVICE_TYPE_CSV:
        for description in CSV_BINARY_SENSORS:
            entities.append(GpsTrackerBinarySensor(hass, entry, description))

    async_add_entities(entities)


class GpsTrackerBinarySensor(GpsTrackerBaseEntity, BinarySensorEntity):
    """A binary sensor for Location Receiver."""

    entity_description: GpsTrackerBinarySensorEntityDescription

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: GpsTrackerBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(hass, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        value = self._data.get(self.entity_description.data_key)
        if value is None:
            return None
        return bool(value)
