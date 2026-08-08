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
| Alarm | problem | |

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

The model, firmware (OTA) version and serial number are filled in on each device.

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
**Configure** button. Each poll costs `1 + 2 × plants + 2 × inverters` cloud requests — 17 for a
single plant with seven microinverters.

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

Every authenticated request needs both `Authorization: Bearer <jwt>` and an `OwnerId` header;
without the latter the backend answers `400 / "Ownerid in http header can't be null"`. Energy
counters arrive in whole watt-hours and are converted to kWh here.

## License

MIT
