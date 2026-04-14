"""Sensor platform for Location Receiver."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPower,
    UnitOfSpeed,
    UnitOfLength,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEVICE_TYPE_CSV, DOMAIN
from .entity import GpsTrackerBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass
class GpsTrackerSensorEntityDescription(SensorEntityDescription):
    """Describe a Location Receiver sensor."""
    data_key: str = ""


SHARED_SENSORS: tuple[GpsTrackerSensorEntityDescription, ...] = (
    GpsTrackerSensorEntityDescription(
        key="battery_level",
        data_key="battery_level",
        name="State of Charge (SOC)",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
    ),
    GpsTrackerSensorEntityDescription(
        key="speed",
        data_key="speed",
        name="Speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        entity_registry_enabled_default=False,
    ),
    GpsTrackerSensorEntityDescription(
        key="altitude",
        data_key="altitude",
        name="Altitude",
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:altimeter",
        entity_registry_enabled_default=False,
    ),
)

CSV_SENSORS: tuple[GpsTrackerSensorEntityDescription, ...] = (
    GpsTrackerSensorEntityDescription(
        key="temperature",
        data_key="temperature",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
    ),
    GpsTrackerSensorEntityDescription(
        key="power",
        data_key="power",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    ),
    GpsTrackerSensorEntityDescription(
        key="gear",
        data_key="gear",
        name="Gear",
        icon="mdi:car-shift-pattern",
        entity_registry_enabled_default=False,
    ),
    GpsTrackerSensorEntityDescription(
        key="ignition",
        data_key="ignition",
        name="Ignition State",
        icon="mdi:key-wireless",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Location Receiver sensors."""
    device_type = entry.data.get("device_type")
    entities: list[GpsTrackerSensor] = []

    for description in SHARED_SENSORS:
        if description.key == "battery_level":
            entities.append(BatterySensor(hass, entry, description))
        else:
            entities.append(GpsTrackerSensor(hass, entry, description))

    if device_type == DEVICE_TYPE_CSV:
        for description in CSV_SENSORS:
            entities.append(GpsTrackerSensor(hass, entry, description))

    async_add_entities(entities)


class GpsTrackerSensor(GpsTrackerBaseEntity, SensorEntity):
    """A sensor entity for Location Receiver."""

    entity_description: GpsTrackerSensorEntityDescription

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: GpsTrackerSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(hass, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._data.get(self.entity_description.data_key)


class BatterySensor(GpsTrackerBaseEntity, SensorEntity, RestoreEntity):
    """State of Charge sensor with state persistence across HA restarts.

    Extends the base sensor with RestoreEntity so the last known battery
    level is immediately available after a restart — without waiting for
    the next webhook from the device.
    """

    entity_description: GpsTrackerSensorEntityDescription

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: GpsTrackerSensorEntityDescription,
    ) -> None:
        """Initialize the battery sensor."""
        super().__init__(hass, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name

    @property
    def native_value(self):
        """Return the current battery level from live payload."""
        return self._data.get(self.entity_description.data_key)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return webhook and device timestamps."""
        if not self._data:
            return None
        attrs = {
            "webhook_received_at": self._data.get("received_at"),
            "device_timestamp":    self._data.get("device_timestamp"),
        }
        return {k: v for k, v in attrs.items() if v is not None} or None

    async def async_added_to_hass(self) -> None:
        """Restore last known state then register live-update callback."""
        await super().async_added_to_hass()  # calls GpsTrackerBaseEntity.async_added_to_hass

        # If no live data was loaded by the base class, try the recorder
        if not self._data:
            restored = await self.async_get_last_state()
            if restored and restored.state not in (None, "unavailable", "unknown"):
                try:
                    # Seed _data with the restored value so available=True
                    # and native_value returns the last known level immediately.
                    self._data = {
                        "battery_level": float(restored.state),
                        "received_at":   restored.attributes.get("webhook_received_at"),
                        "device_timestamp": restored.attributes.get("device_timestamp"),
                    }
                    _LOGGER.debug(
                        "Location Receiver [%s]: restored battery level %s%% from recorder",
                        self._device_name,
                        restored.state,
                    )
                except (TypeError, ValueError):
                    pass

        # Register live-update dispatcher (overrides restored value on first webhook)
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
