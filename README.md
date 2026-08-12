# Rockcore Solar (RC-C) for Home Assistant

Home Assistant integration for **Rockcore** microinverters — the ones monitored through the
**RC-C** app (Shanghai Rockcore Electronic Technology, `studio.yanxinalircc.com` /
[App Store](https://apps.apple.com/us/app/rc-c/id6572295769)).

It signs in to the same cloud the app uses (`app.rc-ess.com`) and exposes the plant and every
microinverter as Home Assistant devices, ready for the **Energy dashboard**.

> Not affiliated with or endorsed by Rockcore. The cloud API is undocumented and may change.

## What you get

**Per plant** (one device):

| Entity | Unit | Notes |
| --- | --- | --- |
| Power | W | Current AC output of the whole plant |
| Energy today / this month / this year | kWh | `total_increasing` |
| **Total energy** | kWh | Lifetime yield — **use this one in the Energy dashboard** |
| Installed capacity | kW | Diagnostic |
| Efficiency | % | Output relative to installed capacity, diagnostic |
| CO2 avoided | kg | Diagnostic |
| Last update | timestamp | Diagnostic |
| Online | connectivity | Diagnostic |
| Active alarms | count | Open (not yet recovered) alarms across the plant's inverters |
| Latest alarm | text | Most recent alarm; level, device and timestamps in the attributes |
| Inverters with isolated alarms | count | Diagnostic; per-inverter breakdown in the attributes — read below |
| Alarm | problem | On while any alarm is open — read the caveat below |

**Per microinverter** (one device each, linked to the plant):

| Entity | Unit | Notes |
| --- | --- | --- |
| Power | W | |
| Energy today / this month / this year / total | kWh | `total_increasing` |
| Grid voltage | V | |
| Grid frequency | Hz | |
| Temperature | °C | |
| Wi-Fi signal | % | Diagnostic |
| Last update | timestamp | Diagnostic |
| PV *n* power | W | One set per MPPT input (an RC8021 has four) |
| PV *n* voltage / current | V / A | Diagnostic |
| Online | connectivity | Diagnostic |
| Alarm | problem | On while this inverter has an open alarm |

The model, firmware (OTA) version and serial number are filled in on each device.

### About the alarms

The plant's own `isAlarm` flag is **not** used: the cloud leaves it `false` even with alarms open
(measured here: `isAlarm: false` while 15 alarms were unrecovered), so an entity driven by it would
never fire. The alarm entities instead count the alarms the cloud has not marked as recovered.

Two things worth knowing before you automate on them:

- **The cloud never clears some alarms.** On the reference plant the four `Channel N undervoltage`
  alarms have been open since commissioning day and have no recovery event, so the `Alarm` entity
  sits at `on` permanently. **Trigger on the `Active alarms` count changing, or on the
  `active_alarm_levels` attribute, not on the state flipping.**
- **Level 1 is routine.** `Hardware Protection` accounts for 175 of 208 alarms on the reference
  plant and self-recovers in ~183 s (median), typically across several inverters at once — that is
  a grid disturbance, not a failing unit. Levels 2 and 3 are the ones that stick.

Both alarm entities carry `active_alarm_count`, `active_alarm_levels` and the full `active_alarms`
list in their attributes.

### Isolated alarms

Counting alarms tells you about the weather, not about your inverters. Over seven days on the
reference plant (335 alarms, seven inverters) the daily alarm count correlated **-0.45** with the
daily yield: the cloudiest day produced 86 alarms and the sunniest 41. 85% of them hit more than one
inverter within two minutes and 31% hit all seven at once, which is a grid event, not a unit fault.

`Inverters with isolated alarms` applies two filters instead:

1. **Isolated** — the alarm only counts if no other inverter reported the same message within two
   minutes. This is what removes the weather: after filtering, the count correlates **+0.31** with
   the yield, and on the cloudiest day only 9 of the 86 alarms survived.
2. **Recurrent** — the inverter is flagged only if it has isolated alarms on at least two of the
   last three days *and* its seven-day total is above the median of the fleet. The median is the
   control: on a bad day every unit collects a few isolated alarms, so the bar rises with them and
   nobody stands out.

What is left is a per-unit signal. On the reference plant two inverters have never produced a single
isolated alarm while one produced 20 across five days, and the same seven days of data flag two
units and stay quiet about the other five.

The attributes carry `alarms_examined`, `isolated_alarms`, `fleet_median`, `flagged_inverters` and a
`per_inverter` breakdown with `isolated`, `days`, `days_recent` and `flagged` for each unit.

**This is a ranking, not a diagnosis, and it is deliberately not wired to any alert.** The
thresholds were calibrated on a week of data that contains no actual failure, so watch the numbers
for a while before you automate on them. It also does not replace comparing the MPPT inputs against
each other: on the reference plant three dead panel connections produced no alarm at all.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories**
2. Repository `https://github.com/bobaoapae/ha-rockcore`, type **Integration**
3. Install **Rockcore Solar (RC-C)** and restart Home Assistant

### Manual

Copy `custom_components/rockcore` into your Home Assistant `config/custom_components/` folder and
restart.

## Setup

**Settings → Devices & services → Add integration → Rockcore Solar (RC-C)**, then enter the same
e-mail and password you use in the RC-C app.

> ⚠️ The Rockcore cloud **locks the account after ten consecutive wrong passwords**. The
> integration never retries a rejected password on its own — it raises a re-authentication prompt
> instead — but do double-check the credentials in the app before retrying by hand.

The polling interval (default **60 s**, range 30–900 s) can be changed under the integration's
**Configure** button. Each poll costs `3 + 2 × plants + 2 × inverters` cloud requests — 19 for a
single plant with seven microinverters. The two alarm calls are per account, not per inverter, so
they do not grow with the plant.

## Energy dashboard

**Settings → Dashboards → Energy → Solar panels → Add solar production**, and pick the plant's
**Total energy** sensor — the lifetime counter, not the daily one. (Its entity id follows your
Home Assistant language, e.g. `sensor.<plant>_total_energy` or `sensor.<plant>_energia_total`.)

Statistics start accumulating from the moment the sensor is added; history from before the
integration existed is not backfilled. The Energy dashboard's hourly figures only appear after the
next top-of-the-hour rollup, so the first hour can look empty.

If you already have another solar system in the dashboard, add this one as a **second** solar
source — Home Assistant sums them.

## How it works

The RC-C app is a thin React Native shell around the web app at `https://app.rc-ess.com/login/app/26`,
which talks to a JowoIoT backend under `/jowoiot-proxy/api/project/rc`:

| Endpoint | Purpose |
| --- | --- |
| `POST /auth/login` | `{channelType, content, password, type}` → JWT + numeric owner id |
| `POST /station/overview/searchStation` | All plants with power and energy counters |
| `GET /station/item/show/{id}` | Live summary of one plant |
| `GET /station/item/info/{id}` | Static plant configuration |
| `POST /station/item/page` | Inverters of a plant, with power and daily yield |
| `GET /device/data/{id}` | Energy counters of one inverter |
| `GET /device/detail/{id}` | Grid voltage/frequency, temperature, Wi-Fi and the MPPT inputs |
| `POST /alarm-notice-set/pageByAreaId` | Open alarms — the only variant that filters `recovered` server-side |
| `POST /alarm-notice-set/page` | Alarm history; `orderByField` only accepts `0` (time) |

Header quirks, all of them load-bearing:

- `POST /auth/login` needs an `oem: rc` header to pick the tenant. Without it the login fails with
  `code 500 / "Not a null user"` — which looks like a credentials error but is not.
- Every authenticated request needs **both** `Authorization: Bearer <jwt>` and an `OwnerId` header;
  without the latter the backend answers `400 / "Ownerid in http header can't be null"`.
- A rejected password comes back as **HTTP 200 with `code: 500`**, while an expired token gives a
  bare **401**. The two are handled differently: 401 triggers a silent re-login and one retry,
  a rejected password never does.

Energy counters arrive in whole watt-hours and are converted to kWh here.

## License

MIT
