"""Base entities for the Rockcore Solar (RC-C) integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import InverterData, RockcoreCoordinator, StationData


class RockcoreStationEntity(CoordinatorEntity[RockcoreCoordinator]):
    """An entity that belongs to a plant."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RockcoreCoordinator, station_id: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"station_{station_id}")},
            manufacturer=MANUFACTURER,
            name=self.station.name,
            model="Plant",
            configuration_url="https://app.rc-ess.com/login/app/26",
        )

    @property
    def station(self) -> StationData:
        """The plant this entity belongs to."""
        return self.coordinator.data[self._station_id]

    @property
    def available(self) -> bool:
        """Whether the last poll returned this plant."""
        return super().available and self._station_id in (self.coordinator.data or {})


class RockcoreInverterEntity(CoordinatorEntity[RockcoreCoordinator]):
    """An entity that belongs to a microinverter."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RockcoreCoordinator, station_id: str, org_id: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._org_id = org_id
        inverter = self.inverter
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"device_{org_id}")},
            manufacturer=MANUFACTURER,
            name=inverter.name,
            model=inverter.model,
            sw_version=inverter.firmware,
            serial_number=inverter.serial,
            via_device=(DOMAIN, f"station_{station_id}"),
            configuration_url="https://app.rc-ess.com/login/app/26",
        )

    @property
    def inverter(self) -> InverterData:
        """The microinverter this entity belongs to."""
        return self.coordinator.data[self._station_id].inverters[self._org_id]

    @property
    def available(self) -> bool:
        """Whether the last poll returned this inverter."""
        if not super().available:
            return False
        station = (self.coordinator.data or {}).get(self._station_id)
        return station is not None and self._org_id in station.inverters
