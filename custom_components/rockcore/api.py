"""Async client for the Rockcore RC-C cloud API.

The RC-C mobile app (``studio.yanxinalircc.com``) is a thin wrapper around the
web app hosted at ``https://app.rc-ess.com``. That web app talks to a JowoIoT
backend under ``/jowoiot-proxy/api/project/rc``. This module speaks the same
protocol.

Authentication
--------------
``POST /auth/login`` takes the e-mail/username and the *plain* password and
returns a JWT (valid for a year) plus the numeric owner id. It also requires the
``oem`` header to pick the tenant — without it the login fails with
``code 500 / "Not a null user"``, which reads like a bad-credentials error but
is not. Every subsequent request needs both ``Authorization: Bearer <token>``
and an ``OwnerId`` header; without the latter the backend answers ``400 /
"Ownerid in http header can't be null"``. An expired or invalid token yields a
bare ``401``.

Note that the backend locks the account after ten consecutive bad passwords, so
callers must never retry a :class:`RockcoreAuthError` in a loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import (
    ALARM_MAX_PAGES,
    ALARM_PAGE_SIZE,
    API_BASE,
    API_OEM,
    API_SCOPE,
    REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class RockcoreError(Exception):
    """Base error for the Rockcore cloud API."""


class RockcoreConnectionError(RockcoreError):
    """The cloud could not be reached."""


class RockcoreAuthError(RockcoreError):
    """The credentials were rejected.

    Never retried automatically: the backend counts failed attempts and locks
    the account after ten of them.
    """


class RockcoreClient:
    """Minimal async client covering the read-only endpoints of the RC-C app."""

    def __init__(
        self,
        session: ClientSession,
        email: str,
        password: str,
        *,
        timezone_offset: float = 0,
    ) -> None:
        """Initialise the client. No I/O happens here."""
        self._session = session
        self._email = email
        self._password = password
        self._timezone_offset = timezone_offset
        self._login_lock = asyncio.Lock()

        self._token: str | None = None
        self._user_id: str | None = None
        self._owner_id: str | None = None
        self._user_name: str | None = None
        self._area_id: str | None = None

    @property
    def user_id(self) -> str | None:
        """IoT user id (``usr_...``), stable per account."""
        return self._user_id

    @property
    def owner_id(self) -> str | None:
        """Numeric owner id required in the ``OwnerId`` header."""
        return self._owner_id

    @property
    def user_name(self) -> str | None:
        """Display name of the logged in account."""
        return self._user_name

    @property
    def area_id(self) -> str | None:
        """Region id of the account, required to filter alarms server-side."""
        return self._area_id

    def _headers(self, *, authenticated: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US",
            "Content-Type": "application/json;charset=utf-8",
            # Mandatory on /auth/login (it selects the OEM tenant); dropping it
            # fails the login with a misleading code 500 "Not a null user".
            "oem": API_OEM,
            "scope": API_SCOPE,
            "Timezone-Offset": str(self._timezone_offset),
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token}"
            headers["OwnerId"] = str(self._owner_id)
            if self._user_id:
                headers["userId"] = self._user_id
        return headers

    async def async_login(self) -> None:
        """Authenticate and cache the token, owner id and user id."""
        async with self._login_lock:
            await self._async_login_locked()

    async def _async_login_locked(self) -> None:
        payload = {
            "channelType": 2,
            "content": self._email,
            "password": self._password,
            "type": "2",
        }
        try:
            async with self._session.post(
                f"{API_BASE}/auth/login",
                json=payload,
                headers=self._headers(authenticated=False),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise RockcoreAuthError("Invalid e-mail or password")
                resp.raise_for_status()
                body = await resp.json(content_type=None)
        except ClientResponseError as err:
            raise RockcoreConnectionError(f"Login failed: HTTP {err.status}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise RockcoreConnectionError(f"Cannot reach the Rockcore cloud: {err}") from err

        # A rejected password comes back as HTTP 200 with code 500 in the body.
        if body.get("code") != 200:
            message = body.get("message") or "Login rejected by the Rockcore cloud"
            raise RockcoreAuthError(message)

        data = body.get("data") or {}
        response = data.get("response") or {}
        token = response.get("accessToken")
        owner_id = (data.get("loginUser") or {}).get("id")
        if not token or not owner_id:
            raise RockcoreAuthError("Login response did not contain a token")

        self._token = token
        self._owner_id = str(owner_id)
        login_user = data.get("loginUser") or {}
        user = response.get("user") or {}
        self._user_id = user.get("userId")
        self._user_name = user.get("userName") or login_user.get("userName")
        area_id = login_user.get("areaId")
        self._area_id = str(area_id) if area_id is not None else None
        _LOGGER.debug("Logged in to the Rockcore cloud as %s", self._user_name)

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a request, logging in (once) if the token is missing or stale."""
        if self._token is None:
            await self.async_login()

        for attempt in range(2):
            try:
                async with self._session.request(
                    method,
                    f"{API_BASE}{path}",
                    json=json,
                    params=params,
                    headers=self._headers(authenticated=True),
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status == 401:
                        # Token expired or revoked: re-authenticate once.
                        if attempt == 0:
                            _LOGGER.debug("Token rejected on %s, re-authenticating", path)
                            self._token = None
                            await self.async_login()
                            continue
                        raise RockcoreAuthError("Token rejected after re-authentication")
                    resp.raise_for_status()
                    body = await resp.json(content_type=None)
            except ClientResponseError as err:
                raise RockcoreConnectionError(f"{path} failed: HTTP {err.status}") from err
            except (ClientError, asyncio.TimeoutError) as err:
                raise RockcoreConnectionError(f"{path} failed: {err}") from err

            if body.get("code") != 200:
                message = body.get("message") or f"code {body.get('code')}"
                raise RockcoreError(f"{path} rejected: {message}")
            return body.get("data")

        raise RockcoreConnectionError(f"{path} failed")

    async def async_get_stations(self) -> list[dict[str, Any]]:
        """Return every plant visible to the account."""
        data = await self._async_request(
            "POST", "/station/overview/searchStation", json={"page": 1, "size": 100}
        )
        if not isinstance(data, dict):
            return []
        content = data.get("content")
        return content if isinstance(content, list) else []

    async def async_get_station(self, station_id: str) -> dict[str, Any]:
        """Return the live summary of one plant."""
        data = await self._async_request("GET", f"/station/item/show/{station_id}")
        return data if isinstance(data, dict) else {}

    async def async_get_station_info(self, station_id: str) -> dict[str, Any]:
        """Return the static configuration of one plant (location, timezone...)."""
        data = await self._async_request(
            "GET", f"/station/item/info/{station_id}", params={"commHost": "", "commPort": ""}
        )
        return data if isinstance(data, dict) else {}

    async def async_get_station_devices(self, station_id: str) -> list[dict[str, Any]]:
        """Return every inverter of a plant with its live power and daily yield."""
        devices: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._async_request(
                "POST",
                "/station/item/page",
                json={"page": page, "size": 50, "stationId": str(station_id)},
            )
            if not isinstance(data, dict):
                break
            content = data.get("content")
            if not isinstance(content, list) or not content:
                break
            devices.extend(content)
            try:
                total = int(data.get("total", len(devices)))
            except (TypeError, ValueError):
                total = len(devices)
            if len(devices) >= total:
                break
            page += 1
        return devices

    async def async_get_device_data(self, org_id: str) -> dict[str, Any]:
        """Return the energy counters of one inverter."""
        data = await self._async_request("GET", f"/device/data/{org_id}")
        return data if isinstance(data, dict) else {}

    async def async_get_device_detail(self, org_id: str) -> dict[str, Any]:
        """Return live electrical values of one inverter, including its PV inputs."""
        data = await self._async_request("GET", f"/device/detail/{org_id}")
        return data if isinstance(data, dict) else {}

    async def async_get_active_alarms(self) -> list[dict[str, Any]]:
        """Return the alarms that have not recovered yet.

        Only ``/alarm-notice-set/pageByAreaId`` can filter on ``recovered``
        server-side, and it takes its filters as query parameters rather than a
        JSON body. Despite the name it is still scoped to what the account may
        see, so no foreign plants leak in.

        The window starts at the epoch on purpose: alarms can stay active for
        weeks (the oldest one here dates from commissioning day), and a rolling
        window would silently drop them and under-report the count.
        """
        return await self._async_list_alarms(start_ms=0, recovered="false")

    async def async_get_recent_alarms(self, days: int) -> list[dict[str, Any]]:
        """Return every alarm of the last ``days``, recovered or not.

        The isolation analysis cannot reuse ``async_get_active_alarms``: the
        alarms it needs are mostly the recovered ones (98% of the level 1 events
        on the reference plant), and they clear in about three minutes, far too
        fast for any poll interval to sample them as they happen.
        """
        start_ms = int((time.time() - days * 86400) * 1000)
        return await self._async_list_alarms(start_ms=start_ms, recovered=None)

    async def _async_list_alarms(
        self, *, start_ms: int, recovered: str | None
    ) -> list[dict[str, Any]]:
        """Page through ``pageByAreaId``, the only listing with server-side filters."""
        if not self._area_id:
            # Without a region we cannot use the server-side filter at all.
            return []

        alarms: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "size": ALARM_PAGE_SIZE,
            "start": start_ms,
            "end": int(time.time() * 1000),
            "areaId": self._area_id,
        }
        if recovered is not None:
            params["recovered"] = recovered

        for page in range(1, ALARM_MAX_PAGES + 1):
            data = await self._async_request(
                "POST", "/alarm-notice-set/pageByAreaId", params={**params, "page": page}
            )
            if not isinstance(data, dict):
                break
            content = data.get("content")
            if not isinstance(content, list) or not content:
                break
            alarms.extend(content)
            try:
                total = int(data.get("total", len(alarms)))
            except (TypeError, ValueError):
                total = len(alarms)
            if len(alarms) >= total:
                break
        else:
            _LOGGER.warning(
                "Stopped after %s pages of alarms; the list may be truncated",
                ALARM_MAX_PAGES,
            )
        return alarms

    async def async_get_latest_alarm(self) -> dict[str, Any] | None:
        """Return the most recent alarm, recovered or not.

        ``orderByField`` only accepts 0 (time); 1 and 2 make the backend throw.
        """
        now_ms = int(time.time() * 1000)
        data = await self._async_request(
            "POST",
            "/alarm-notice-set/page",
            json={
                "page": 1,
                "row": 1,
                "start": 0,
                "end": now_ms,
                "orderByField": 0,
                "orderByAscOrDesc": 0,
            },
        )
        if not isinstance(data, dict):
            return None
        content = data.get("content")
        if isinstance(content, list) and content:
            return content[0]
        return None
