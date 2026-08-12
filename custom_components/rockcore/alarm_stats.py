"""Isolation and recurrence analysis of the cloud alarms.

An "any alarm open" signal is useless on this platform, and a plain alarm count
is worse than useless: it tracks the weather. Measured on the reference plant
over seven days (335 alarms, seven inverters):

* 85% of the alarms hit more than one inverter within two minutes and 31% hit
  all seven at once. Those are grid or weather events, not a unit fault. The
  total count correlates **-0.45** with the daily yield, so the worst day
  produced the most alarms (86 on a 6.8 kWh day against 41 on a 90.8 kWh day).
* Dropping every alarm that another inverter shared at that moment removes the
  weather dependency: the isolated count correlates **+0.31** with the yield,
  and on the cloudiest day only 9 of 86 alarms survived the filter.
* What is left is a per-unit signal. Two inverters have never produced a single
  isolated alarm, while one produced 20 spread over five days.

So an alarm is only worth surfacing when it is *isolated* (nobody else
complained at the same moment) and *recurrent* (the same unit keeps doing it).
The fleet median is the control for the second half: on a bad day every unit
collects a few isolated alarms, the median rises with them, and nobody stands
out — exactly the behaviour a fixed threshold fails to get right.

Nothing here decides that an inverter is faulty. It ranks units against their
own peers so a developing problem shows up before the energy counters move.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from homeassistant.util import dt as dt_util


@dataclass(slots=True)
class DeviceAlarmStats:
    """Isolated-alarm figures of a single inverter."""

    #: Alarms in the window that no other inverter reported at the same time.
    isolated: int = 0
    #: Distinct days of the window on which this inverter had an isolated alarm.
    isolated_days: int = 0
    #: How many of the most recent days are among those.
    isolated_days_recent: int = 0
    #: Isolated and recurrent, and above the fleet median.
    flagged: bool = False


@dataclass(slots=True)
class AlarmStats:
    """Fleet-wide result of the analysis."""

    per_device: dict[str, DeviceAlarmStats] = field(default_factory=dict)
    #: Alarms examined, before any filtering.
    total: int = 0
    #: Of those, the ones no other inverter shared.
    isolated_total: int = 0
    #: Median of the per-inverter isolated counts, the bar a unit has to clear.
    median_isolated: float = 0.0

    @property
    def flagged(self) -> list[str]:
        """Inverters that are both isolated and recurrent offenders."""
        return sorted(name for name, stats in self.per_device.items() if stats.flagged)


def _alarm_time(alarm: dict[str, Any]) -> datetime | None:
    """Timestamp of an alarm, as a millisecond epoch in ``time``/``createTime``."""
    raw = alarm.get("time") or alarm.get("createTime")
    try:
        millis = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not millis:
        return None
    return dt_util.utc_from_timestamp(millis / 1000)


def compute_alarm_stats(
    alarms: Iterable[dict[str, Any]],
    devices: Iterable[str],
    *,
    window: timedelta,
    recent_days: int,
    recent_days_min: int,
    now: datetime | None = None,
) -> AlarmStats:
    """Score every inverter by its isolated, recurrent alarms.

    ``devices`` is the whole fleet, not only the units that alarmed: a unit with
    no alarm at all still has to count towards the median, otherwise the bar is
    set by the offenders alone and everyone clears it.
    """
    now = now or dt_util.utcnow()
    dated = [(alarm, when) for alarm in alarms if (when := _alarm_time(alarm))]

    # Same message within the window on another inverter means the cause was
    # shared, so the alarm says nothing about this particular unit.
    by_message: dict[str, list[tuple[str, datetime]]] = {}
    for alarm, when in dated:
        name = str(alarm.get("deviceName") or "")
        by_message.setdefault(str(alarm.get("message") or ""), []).append((name, when))

    stats = {name: DeviceAlarmStats() for name in devices}
    isolated_days: dict[str, set[Any]] = {name: set() for name in stats}
    isolated_total = 0

    for alarm, when in dated:
        name = str(alarm.get("deviceName") or "")
        if name not in stats:
            continue
        peers = by_message.get(str(alarm.get("message") or ""), ())
        if any(
            other != name
            and abs((moment - when).total_seconds()) <= window.total_seconds()
            for other, moment in peers
        ):
            continue
        isolated_total += 1
        stats[name].isolated += 1
        isolated_days[name].add(dt_util.as_local(when).date())

    recent_cutoff = dt_util.as_local(now).date() - timedelta(days=recent_days - 1)
    for name, days in isolated_days.items():
        stats[name].isolated_days = len(days)
        stats[name].isolated_days_recent = sum(
            1 for day in days if day >= recent_cutoff
        )

    bar = median(entry.isolated for entry in stats.values()) if stats else 0.0
    for entry in stats.values():
        entry.flagged = (
            entry.isolated_days_recent >= recent_days_min and entry.isolated > bar
        )

    return AlarmStats(
        per_device=stats,
        total=len(dated),
        isolated_total=isolated_total,
        median_isolated=float(bar),
    )
