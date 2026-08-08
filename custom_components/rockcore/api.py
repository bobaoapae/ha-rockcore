"""Async client for the Rockcore RC-C cloud API.

The RC-C mobile app (``studio.yanxinalircc.com``) is a thin wrapper around the
web app hosted at ``https://app.rc-ess.com``. That web app talks to a JowoIoT
backend under ``/jowoiot-proxy/api/project/rc``. This module speaks the same
protocol.

Authentication
--------------
``POST /auth/login`` takes the e-mail/username and the *plain* password and
returns a JWT (valid for a year) plus the numeric owner id. Every subsequent
request needs both ``Authorization: Bearer <token>`` and an ``OwnerId`` header;
without the latter the backend answers ``400 / "Ownerid in http header can't be
null"``. An expired or invalid token yields a bare ``401``.

Note that the backend locks the account after ten consecutive bad passwords, so
callers must never retry a :class:`RockcoreAuthError` in a loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import API_BASE, API_OEM, API_SCOPE, REQUEST_TIMEOUT

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

    def _headers(self, *, authenticated: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US",
            "Content-Type": "application/json;charset=utf-8",
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
        user = response.get("user") or {}
        self._user_id = user.get("userId")
        self._user_name = user.get("userName") or (data.get("loginUser") or {}).get("userName")
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
