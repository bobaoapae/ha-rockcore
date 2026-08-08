"""Sensors for the Rockcore Solar (RC-C) integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfMass,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import InverterData, RockcoreConfigEntry, RockcoreCoordinator, StationData
from .entity import RockcoreInverterEntity, RockcoreStationEntity


def _as_float(value: Any) -> float | None:
    """Coerce the loosely typed API values (str, int, float, None) to a float."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _wh_to_kwh(value: Any) -> float | None:
    """The API reports energy counters in whole watt-hours."""
    watt_hours = _as_float(value)
    return None if watt_hours is None else watt_hours / 1000


def _lifetime_kwh(value: Any) -> float | None:
    """Lifetime yield, guarding against a spurious zero from the cloud.

    These sensors are ``total_increasing``, so a drop to zero would be read as a
    meter reset and the whole counter would be added to the statistics a second
    time. A commissioned plant never goes back to zero lifetime production, so
    treat a reported 0 as "unknown" and let the next poll fill the gap.
    """
    kwh = _wh_to_kwh(value)
    return None if not kwh else kwh


def _tonnes_to_kg(value: Any) -> float | None:
    """The eco counters are reported in tonnes."""
    tonnes = _as_float(value)
    return None if tonnes is None else tonnes * 1000


def _as_timestamp(value: Any) -> datetime | None:
    """``lastUpdateTime`` is a millisecond epoch, delivered as a string."""
    millis = _as_float(value)
    if not millis:
        return None
    return dt_util.utc_from_timestamp(millis / 1000)


def _latest_alarm_attrs(station: StationData) -> dict[str, Any]:
    """Detail of the most recent alarm, for the attribute dictionary."""
    alarm = station.latest_alarm or {}
    return {
        "level": alarm.get("alarmLevel"),
        "device": alarm.get("deviceName"),
        "recovered": alarm.get("recovered"),
        "confirmed": alarm.get("confirmed"),
        "time": _as_timestamp(alarm.get("time")),
        "recovered_at": _as_timestamp(alarm.get("recoverTime")),
    }


@dataclass(frozen=True, kw_only=True)
class RockcoreStationSensorDescription(SensorEntityDescription):
    """Describes a plant-level sensor."""

    value_fn: Callable[[StationData], Any]
    attrs_fn: Callable[[StationData], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class RockcoreInverterSensorDescription(SensorEntityDescription):
    """Describes an inverter-level sensor."""

    value_fn: Callable[[InverterData], Any]


STATION_SENSORS: tuple[RockcoreStationSensorDescription, ...] = (
    RockcoreStationSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda station: _as_float(station.info.get("power")),
    ),
    RockcoreStationSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda station: _wh_to_kwh(station.info.get("dayEnergy")),
    ),
    RockcoreStationSensorDescription(
        key="energy_month",
        translation_key="energy_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda station: _wh_to_kwh(station.info.get("monthEnergy")),
    ),
    RockcoreStationSensorDescription(
        key="energy_year",
        translation_key="energy_year",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda station: _wh_to_kwh(station.info.get("yearEnergy")),
    ),
    RockcoreStationSensorDescription(
        key="energy_total",
        translation_key="energy_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda station: _lifetime_kwh(station.info.get("accEnergy")),
    ),
    RockcoreStationSensorDescription(
        key="capacity",
        translation_key="capacity",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: _as_float(station.info.get("installCapacity")),
    ),
    RockcoreStationSensorDescription(
        key="efficiency",
        translation_key="efficiency",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: _as_float(station.info.get("efficiency")),
    ),
    RockcoreStationSensorDescription(
        key="co2_avoided",
        translation_key="co2_avoided",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: _tonnes_to_kg(station.info.get("saveCarbonNum")),
    ),
    RockcoreStationSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: _as_timestamp(station.info.get("lastUpdateTime")),
    ),
    RockcoreStationSensorDescription(
        key="active_alarms",
        translation_key="active_alarms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda station: len(station.active_alarms),
    ),
    RockcoreStationSensorDescription(
        key="latest_alarm",
        translation_key="latest_alarm",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: (station.latest_alarm or {}).get("message"),
        attrs_fn=_latest_alarm_attrs,
    ),
)


INVERTER_SENSORS: tuple[RockcoreInverterSensorDescription, ...] = (
    RockcoreInverterSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda inv: _as_float(inv.data.get("power", inv.summary.get("power"))),
    ),
    RockcoreInverterSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda inv: _wh_to_kwh(inv.data.get("dayEnergy", inv.summary.get("energy"))),
    ),
    RockcoreInverterSensorDescription(
        key="energy_month",
        translation_key="energy_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda inv: _wh_to_kwh(inv.data.get("monthEnergy")),
    ),
    RockcoreInverterSensorDescription(
        key="energy_year",
        translation_key="energy_year",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda inv: _wh_to_kwh(inv.data.get("yearEnergy")),
    ),
    RockcoreInverterSensorDescription(
        key="energy_total",
        translation_key="energy_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda inv: _lifetime_kwh(inv.data.get("accEnergy")),
    ),
    RockcoreInverterSensorDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda inv: _as_float(inv.detail.get("volt")),
    ),
    RockcoreInverterSensorDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda inv: _as_float(inv.detail.get("frequency")),
    ),
    RockcoreInverterSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda inv: _as_float(inv.detail.get("temperature")),
    ),
    RockcoreInverterSensorDescription(
        key="wifi_strength",
        translation_key="wifi_strength",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda inv: _as_float(inv.detail.get("wifiStrength")),
    ),
    RockcoreInverterSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda inv: _as_timestamp(
            inv.detail.get("lastUpdateTime", inv.summary.get("lastUpdateTime"))
        ),
    ),
)


def _pv_descriptions(index: int) -> tuple[RockcoreInverterSensorDescription, ...]:
    """Build the three sensors of one MPPT input (1-based ``index``)."""
    slot = index - 1

    def _field(name: str) -> Callable[[InverterData], Any]:
        def _value(inv: InverterData) -> Any:
            inputs = inv.pv_inputs
            if slot >= len(inputs):
                return None
            return _as_float(inputs[slot].get(name))

        return _value

    return (
        RockcoreInverterSensorDescription(
            key=f"pv{index}_power",
            translation_key="pv_power",
            translation_placeholders={"index": str(index)},
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
            value_fn=_field("power"),
        ),
        RockcoreInverterSensorDescription(
            key=f"pv{index}_voltage",
            translation_key="pv_voltage",
            translation_placeholders={"index": str(index)},
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_field("volt"),
        ),
        RockcoreInverterSensorDescription(
            key=f"pv{index}_current",
            translation_key="pv_current",
            translation_placeholders={"index": str(index)},
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_field("current"),
        ),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RockcoreConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Rockcore sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    for station_id, station in coordinator.data.items():
        entities.extend(
            RockcoreStationSensor(coordinator, station_id, description)
            for description in STATION_SENSORS
        )
        for org_id, inverter in station.inverters.items():
            entities.extend(
                RockcoreInverterSensor(coordinator, station_id, org_id, description)
                for description in INVERTER_SENSORS
            )
            for index in range(1, len(inverter.pv_inputs) + 1):
                entities.extend(
                    RockcoreInverterSensor(coordinator, station_id, org_id, description)
                    for description in _pv_descriptions(index)
                )

    async_add_entities(entities)


class RockcoreStationSensor(RockcoreStationEntity, SensorEntity):
    """A plant-level sensor."""

    entity_description: RockcoreStationSensorDescription

    def __init__(
        self,
        coordinator: RockcoreCoordinator,
        station_id: str,
        description: RockcoreStationSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, station_id)
        self.entity_description = description
        self._attr_unique_id = f"{station_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.station)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the extra detail this sensor carries, if any."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.station)


class RockcoreInverterSensor(RockcoreInverterEntity, SensorEntity):
    """An inverter-level sensor."""

    entity_description: RockcoreInverterSensorDescription

    def __init__(
        self,
        coordinator: RockcoreCoordinator,
        station_id: str,
        org_id: str,
        description: RockcoreInverterSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, station_id, org_id)
        self.entity_description = description
        self._attr_unique_id = f"{org_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.inverter)
