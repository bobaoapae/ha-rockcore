"""Data coordinator for the Rockcore Solar (RC-C) integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RockcoreAuthError, RockcoreClient, RockcoreError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

type RockcoreConfigEntry = ConfigEntry[RockcoreCoordinator]


@dataclass(slots=True)
class InverterData:
    """Everything known about a single microinverter."""

    org_id: str
    #: Row from ``/station/item/page`` (name, sn, power, energy, isOnline...).
    summary: dict[str, Any] = field(default_factory=dict)
    #: ``/device/data/{id}`` — the energy counters (day/month/year/total).
    data: dict[str, Any] = field(default_factory=dict)
    #: ``/device/detail/{id}`` — grid voltage, frequency, temperature, pvList.
    detail: dict[str, Any] = field(default_factory=dict)
    #: Alarms of this inverter that have not recovered yet.
    active_alarms: list[dict[str, Any]] = field(default_factory=list)

    @property
    def serial(self) -> str:
        """Serial number as printed on the unit."""
        return str(self.summary.get("sn") or self.detail.get("sn") or self.org_id)

    @property
    def mdm_id(self) -> str | None:
        """UUID-style device id (``dev_...``), the one alarms are tagged with."""
        mdm = self.summary.get("devMdmId") or self.detail.get("devMdmId")
        return str(mdm) if mdm else None

    @property
    def name(self) -> str:
        """Display name, which defaults to the serial number in the app."""
        return str(self.summary.get("name") or self.detail.get("name") or self.serial)

    @property
    def model(self) -> str | None:
        """Hardware model, e.g. ``RC8021``."""
        model = self.data.get("model") or self.detail.get("model")
        return str(model) if model else None

    @property
    def firmware(self) -> str | None:
        """Firmware (OTA) version reported by the inverter."""
        version = self.data.get("otaVersion") or self.detail.get("otaVersion")
        return str(version) if version else None

    @property
    def pv_inputs(self) -> list[dict[str, Any]]:
        """The per-panel MPPT inputs of this inverter."""
        pv_list = self.detail.get("pvList")
        return pv_list if isinstance(pv_list, list) else []

    def merged(self) -> dict[str, Any]:
        """All three payloads flattened, later sources winning."""
        return {**self.summary, **self.data, **self.detail}


@dataclass(slots=True)
class StationData:
    """Everything known about a single plant."""

    station_id: str
    #: Row from ``/station/overview/searchStation`` merged with ``/station/item/show``.
    info: dict[str, Any] = field(default_factory=dict)
    #: Static plant configuration from ``/station/item/info``.
    config: dict[str, Any] = field(default_factory=dict)
    inverters: dict[str, InverterData] = field(default_factory=dict)
    #: Alarms of this plant's inverters that have not recovered yet.
    active_alarms: list[dict[str, Any]] = field(default_factory=list)
    #: Most recent alarm of the account, recovered or not.
    latest_alarm: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        """Plant name as shown in the app."""
        return str(self.info.get("name") or f"Plant {self.station_id}")


class RockcoreCoordinator(DataUpdateCoordinator[dict[str, StationData]]):
    """Poll the RC-C cloud and expose plants and inverters."""

    config_entry: RockcoreConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: RockcoreConfigEntry,
        client: RockcoreClient,
        update_interval: timedelta,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, StationData]:
        """Fetch plants, their inverters and the live values of each inverter."""
        try:
            return await self._async_fetch()
        except RockcoreAuthError as err:
            # Surfaced to the user as a re-auth flow instead of a retry loop:
            # the backend locks the account after ten bad passwords.
            raise ConfigEntryAuthFailed(str(err)) from err
        except RockcoreError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_fetch(self) -> dict[str, StationData]:
        stations_raw = await self.client.async_get_stations()
        if not stations_raw:
            raise UpdateFailed("The account has no plants")

        stations: dict[str, StationData] = {}
        for raw in stations_raw:
            station_id = str(raw.get("id") or raw.get("orgId") or "")
            if not station_id:
                continue
            stations[station_id] = StationData(station_id=station_id, info=dict(raw))

        # Per plant: the live summary plus the inverter roster. The search
        # endpoint is the one the app's home screen uses, so it wins on the
        # fields both return (notably installCapacity, which /show reports as
        # the declared array size rather than the sum of the inverters).
        async def _load_station(station: StationData) -> None:
            show, devices = await asyncio.gather(
                self.client.async_get_station(station.station_id),
                self.client.async_get_station_devices(station.station_id),
            )
            station.info = {**show, **station.info}
            for row in devices:
                org_id = str(row.get("orgId") or "")
                if not org_id:
                    continue
                station.inverters[org_id] = InverterData(org_id=org_id, summary=dict(row))

        await asyncio.gather(*(_load_station(station) for station in stations.values()))

        # Per inverter: energy counters and live electrical values.
        async def _load_inverter(inverter: InverterData) -> None:
            data, detail = await asyncio.gather(
                self.client.async_get_device_data(inverter.org_id),
                self.client.async_get_device_detail(inverter.org_id),
            )
            inverter.data = data
            inverter.detail = detail

        await asyncio.gather(
            *(
                _load_inverter(inverter)
                for station in stations.values()
                for inverter in station.inverters.values()
            )
        )

        await self._async_load_alarms(stations)
        return stations

    async def _async_load_alarms(self, stations: dict[str, StationData]) -> None:
        """Attach the open alarms to the plants and inverters they belong to.

        Alarms are listed per account, not per plant, so they are bucketed here
        by device. The plant's ``isAlarm`` flag is deliberately ignored: it stays
        false even while alarms are open, so it never reflected reality.
        """
        active, latest = await asyncio.gather(
            self.client.async_get_active_alarms(),
            self.client.async_get_latest_alarm(),
        )

        # An alarm carries the device UUID and the serial, never the numeric id.
        by_mdm: dict[str, InverterData] = {}
        by_serial: dict[str, InverterData] = {}
        for station in stations.values():
            for inverter in station.inverters.values():
                inverter.active_alarms = []
                if mdm := inverter.mdm_id:
                    by_mdm[mdm] = inverter
                by_serial[inverter.serial] = inverter
            station.active_alarms = []
            station.latest_alarm = latest

        owners: dict[str, StationData] = {
            org_id: station
            for station in stations.values()
            for org_id in station.inverters
        }

        unmatched = 0
        for alarm in active:
            device_id = alarm.get("deviceId")
            name = alarm.get("deviceName")
            inverter = by_mdm.get(str(device_id)) if device_id else None
            if inverter is None and name:
                inverter = by_serial.get(str(name))
            if inverter is None:
                unmatched += 1
                continue
            inverter.active_alarms.append(alarm)
            if station := owners.get(inverter.org_id):
                station.active_alarms.append(alarm)

        if unmatched:
            _LOGGER.debug("%s open alarm(s) did not match a known inverter", unmatched)

    async def async_load_station_config(self) -> None:
        """Fetch the static plant configuration once, for device metadata."""
        if not self.data:
            return
        for station in self.data.values():
            try:
                station.config = await self.client.async_get_station_info(station.station_id)
            except RockcoreError as err:  # non-fatal, only used for extra attributes
                _LOGGER.debug("Could not load config of plant %s: %s", station.station_id, err)
