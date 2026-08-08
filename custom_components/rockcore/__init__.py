"""The Rockcore Solar (RC-C) integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import RockcoreAuthError, RockcoreClient, RockcoreError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .coordinator import RockcoreConfigEntry, RockcoreCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


def _timezone_offset_hours() -> float:
    """The RC-C app sends the local UTC offset in hours; mirror it.

    ``dt_util.now()`` already resolves to the timezone configured in Home
    Assistant, so this needs nothing from ``hass``.
    """
    offset = dt_util.now().utcoffset()
    if offset is None:
        return 0
    return round(offset.total_seconds() / 3600, 2)


async def async_setup_entry(hass: HomeAssistant, entry: RockcoreConfigEntry) -> bool:
    """Set up Rockcore Solar from a config entry."""
    client = RockcoreClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        timezone_offset=_timezone_offset_hours(),
    )

    try:
        await client.async_login()
    except RockcoreAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except RockcoreError as err:
        raise ConfigEntryNotReady(str(err)) from err

    seconds = entry.options.get(CONF_SCAN_INTERVAL)
    interval = timedelta(seconds=seconds) if seconds else DEFAULT_SCAN_INTERVAL

    coordinator = RockcoreCoordinator(hass, entry, client, interval)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_load_station_config()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RockcoreConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: RockcoreConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
