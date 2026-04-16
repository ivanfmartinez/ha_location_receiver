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
    EntityCategory,
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
from homeassistant.util import dt as dt_util

from .const import ATTR_FORMAT, ENTITY_DEVICE_TIMESTAMP, ATTR_WEBHOOK_RECEIVED_AT, DEVICE_TYPE_CSV, DEVICE_TYPE_OSMAND, DOMAIN, ENTITY_ACTIVITY, ENTITY_ALTITUDE, ENTITY_BATTERY_LEVEL, ENTITY_EVENT, ENTITY_GEAR, ENTITY_IGNITION, ENTITY_ODOMETER, ENTITY_POWER, ENTITY_SPEED, ENTITY_TEMPERATURE
from .entity import GpsTrackerBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass
class GpsTrackerSensorEntityDescription(SensorEntityDescription):
    """Describe a Location Receiver sensor."""
    data_key: str = ""


SHARED_SENSORS: tuple[GpsTrackerSensorEntityDescription, ...] = (
    GpsTrackerSensorEntityDescription(
        key="battery_level",
        data_key=ENTITY_BATTERY_LEVEL,
        name="State of Charge (SOC)",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
    ),
    GpsTrackerSensorEntityDescription(
        key="speed",
        data_key=ENTITY_SPEED,
        name="Speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        entity_registry_enabled_default=False,
    ),
    GpsTrackerSensorEntityDescription(
        key="altitude",
        data_key=ENTITY_ALTITUDE,
        name="Altitude",
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:altimeter",
        entity_registry_enabled_default=False,
    ),
    GpsTrackerSensorEntityDescription(
        key="last_reported_location_timestamp",
        data_key=ENTITY_DEVICE_TIMESTAMP,
        name="Last Reported Location Timestamp",
        native_unit_of_measurement=None,
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-end",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

CSV_SENSORS: tuple[GpsTrackerSensorEntityDescription, ...] = (
    GpsTrackerSensorEntityDescription(
        key="temperature",
        data_key=ENTITY_TEMPERATURE,
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
    ),
    GpsTrackerSensorEntityDescription(
        key="power",
        data_key=ENTITY_POWER,
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    ),
    GpsTrackerSensorEntityDescription(
        key="gear",
        data_key=ENTITY_GEAR,
        name="Gear",
        icon="mdi:car-shift-pattern",
        entity_registry_enabled_default=False,
    ),
    GpsTrackerSensorEntityDescription(
        key="ignition",
        data_key=ENTITY_IGNITION,
        name="Ignition State",
        icon="mdi:key-wireless",
    ),
)

OSMAND_SENSORS: tuple[GpsTrackerSensorEntityDescription, ...] = (
    GpsTrackerSensorEntityDescription(
        key="odometer",
        data_key=ENTITY_ODOMETER,
        name="Odometer",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:car-cruise-control",
        entity_registry_enabled_default=False,
    ),
    GpsTrackerSensorEntityDescription(
        key="activity",
        data_key=ENTITY_ACTIVITY,
        name="Activity",
        icon="mdi:run",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    GpsTrackerSensorEntityDescription(
        key="event",
        data_key=ENTITY_EVENT,
        name="Event",
        icon="mdi:bell",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
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
        if description.key == ENTITY_BATTERY_LEVEL:
            entities.append(BatterySensor(hass, entry, description))
        else:
            entities.append(GpsTrackerSensor(hass, entry, description))

    if device_type == DEVICE_TYPE_CSV:
        for description in CSV_SENSORS:
            entities.append(GpsTrackerSensor(hass, entry, description))

    if device_type == DEVICE_TYPE_OSMAND:
        for description in OSMAND_SENSORS:
            entities.append(GpsTrackerSensor(hass, entry, description))

    async_add_entities(entities)


class GpsTrackerSensor(GpsTrackerBaseEntity, SensorEntity, RestoreEntity):
    """A sensor entity for Location Receiver with state persistence across HA restarts.

    Extends the base sensor with RestoreEntity so the last known battery
    level is immediately available after a restart — without waiting for
    the next webhook from the device."""

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
        value = self._data.get(self.entity_description.data_key)

        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(value) if value else None

        return value

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return attributes for entity."""
        if not self._data:
            return None

        if self.entity_description.data_key != ENTITY_DEVICE_TIMESTAMP:
            return None

        attrs = {
            ATTR_WEBHOOK_RECEIVED_AT: self._data.get(ATTR_WEBHOOK_RECEIVED_AT),
            ATTR_FORMAT:  self._data.get(ATTR_FORMAT),
            ENTITY_EVENT:  self._data.get(ENTITY_EVENT),
        }

        attributes = {k: v for k, v in attrs.items() if v is not None} or None
        _LOGGER.debug(f"extra_state_attributes for {self.entity_description.name}: {attributes}")
        return attributes

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
                        self.entity_description.data_key: restored.state,
                    }

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
                        ENTITY_BATTERY_LEVEL: float(restored.state),
                        ATTR_WEBHOOK_RECEIVED_AT:   restored.attributes.get(ATTR_WEBHOOK_RECEIVED_AT),
                        ENTITY_DEVICE_TIMESTAMP: restored.attributes.get(ENTITY_DEVICE_TIMESTAMP),
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
