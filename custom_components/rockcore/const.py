"""Constants for the Rockcore Solar (RC-C) integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "rockcore"

MANUFACTURER: Final = "Rockcore"

CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 900

#: Base URL of the cloud used by the RC-C mobile app (JowoIoT platform, OEM "rc").
API_BASE: Final = "https://app.rc-ess.com/jowoiot-proxy/api/project/rc"

#: The RC-C app identifies itself with these OEM/scope headers. They are not
#: strictly required by the backend, but we mirror the app to stay compatible.
API_OEM: Final = "rc"
API_SCOPE: Final = "yx_test"

REQUEST_TIMEOUT: Final = 30

#: Page size used when listing alarms. The backend rejects anything above 100
#: with ``400 / "request param invalid"``.
ALARM_PAGE_SIZE: Final = 100

#: Safety net so a pathological account cannot spin through endless pages.
ALARM_MAX_PAGES: Final = 5

#: How far back the isolation analysis looks. Long enough for recurrence to
#: mean something, short enough that a unit repaired last week stops counting.
ALARM_HISTORY_DAYS: Final = 7

#: Two alarms of the same kind this close together share a cause. Measured on
#: the reference plant: grid events reach every inverter within a few seconds,
#: so anything wider only makes the filter more conservative.
ALARM_COINCIDENCE_WINDOW: Final = timedelta(seconds=120)

#: A unit has to reoffend in this many of the last ``ALARM_RECENT_DAYS`` days
#: before it is flagged, which is what separates a developing fault from the
#: one-off blip every inverter produces now and then.
ALARM_RECENT_DAYS: Final = 3
ALARM_RECENT_DAYS_MIN: Final = 2
