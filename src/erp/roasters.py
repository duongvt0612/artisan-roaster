#!/usr/bin/python
#
# roasters.py
#
# ABOUT
# Client of the ERP roaster lease API (/api/roasters): list the roasters with their occupation, and claim /
# renew / release one. At any time only ONE logged in session may hold a roaster; the ERP decides atomically.
# Only RoasterError subclasses leave this module.

# LICENSE
# This program or module is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published
# by the Free Software Foundation, either version 2 of the License, or
# version 3 of the License, or (at your option) any later version. It is
# provided for educational purposes and is distributed in the hope that
# it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
# the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from erp import config
from erp.client import ErpAuthError
from erp.session import session

_log: Final[logging.Logger] = logging.getLogger(__name__)


class RoasterError(Exception):
    """Base class; `message` is meant for the user (the ERP answers in Vietnamese)."""

    def __init__(self, message:str) -> None:
        super().__init__(message)
        self.message:str = message


class RoasterOccupied(RoasterError):
    """Somebody else holds the roaster (the message says who)."""


class LeaseLost(RoasterError):
    """This session does not hold the roaster any more (expired and taken over, forced off, or released)."""


class NotHeld(RoasterError):
    """Force release of a roaster nobody holds."""


class RoasterNotFound(RoasterError):
    """No such roaster, or it is not an active roasting machine."""


class RoasterForbidden(RoasterError):
    """The account may not operate roasters (permission roasters:operate / roasters:force-release)."""


class SessionExpired(RoasterError):
    """The ERP login is not valid any more; the user has to log in again."""


class RoasterUnavailable(RoasterError):
    """The ERP could not be asked or failed: kind is 'network', 'server' or 'rate_limited'. Worth a retry."""

    def __init__(self, kind:str, message:str) -> None:
        super().__init__(message)
        self.kind:str = kind


@dataclass(frozen=True)
class Holder:
    user_id:str
    full_name:str
    device_name:str
    since:datetime|None


@dataclass(frozen=True)
class Roaster:
    id:str
    code:str
    name:str
    capacity:str|None
    capacity_unit:str|None
    status:str
    held_by:Holder|None
    version:int
    mine:bool            # held by this user (in any session)
    held_by_session:bool # held by exactly this run of Artisan

    @property
    def is_free(self) -> bool:
        return self.status == 'free'


@dataclass(frozen=True)
class Claim:
    equipment_id:str
    outcome:str
    version:int
    expires_at:datetime|None
    ttl_seconds:int
    heartbeat_seconds:int


def _dt(value:Any) -> datetime|None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _message(body:Any, default:str) -> str:
    if isinstance(body, dict):
        messages = body.get('errorMessages')
        if isinstance(messages, list) and messages and isinstance(messages[0], str) and messages[0]:
            return messages[0]
    return default


def _code(body:Any) -> str:
    if isinstance(body, dict):
        errors = body.get('errors')
        if isinstance(errors, list) and errors and isinstance(errors[0], str):
            return errors[0]
    return ''


def _json(response:Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _request(method:str, url:str, *, json:Any = None, params:dict[str, Any]|None = None) -> Any:
    """Returns the "data" of a successful reply, raises a RoasterError otherwise."""
    try:
        response = session.authed_request(method, url, json=json, params=params)
    except ErpAuthError as e:
        if e.kind == 'invalid_session':
            raise SessionExpired(e.message) from e
        raise RoasterUnavailable(e.kind, e.message) from e
    status = response.status_code
    body = _json(response)
    if 200 <= status < 300:
        if isinstance(body, dict) and body.get('success') is not False and 'data' in body:
            return body['data']
        raise RoasterUnavailable('server', 'Phản hồi của ERP không đúng định dạng')
    message = _message(body, f'HTTP {status}')
    if status == 401:
        raise SessionExpired(message)
    if status == 403:
        raise RoasterForbidden(message)
    if status == 404:
        raise RoasterNotFound(message)
    if status == 409:
        code = _code(body)
        if code == 'ROASTER_OCCUPIED':
            raise RoasterOccupied(message)
        if code == 'LEASE_LOST':
            raise LeaseLost(message)
        if code == 'ROASTER_NOT_HELD':
            raise NotHeld(message)
    if status == 429:
        raise RoasterUnavailable('rate_limited', message)
    if status >= 500:
        raise RoasterUnavailable('server', message)
    raise RoasterError(message)


def _roaster(item:Any) -> Roaster|None:
    try:
        roaster_id = item['id']
        if not isinstance(roaster_id, str) or not roaster_id:
            raise KeyError('id')
        held_by = None
        raw = item.get('heldBy')
        if isinstance(raw, dict):
            held_by = Holder(str(raw.get('userId') or ''), str(raw.get('fullName') or ''), str(raw.get('deviceName') or ''), _dt(raw.get('since')))
        capacity = item.get('capacity')
        unit = item.get('capacityUnit')
        return Roaster(
            id=roaster_id,
            code=str(item.get('code') or ''),
            name=str(item.get('name') or ''),
            capacity=str(capacity) if capacity is not None else None,
            capacity_unit=str(unit) if unit else None,
            status=str(item.get('status') or 'free'),
            held_by=held_by,
            version=int(item.get('version') or 0),
            mine=bool(item.get('mine')),
            held_by_session=bool(item.get('heldBySession')),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        _log.info('skipping a malformed roaster entry')
        return None


def list_roasters(session_id:str) -> list[Roaster]:
    data = _request('GET', config.get_roasters_url(), params={'sessionId': session_id})
    if not isinstance(data, list):
        raise RoasterUnavailable('server', 'Phản hồi của ERP không đúng định dạng')
    return [r for r in (_roaster(item) for item in data) if r is not None]


def claim(roaster_id:str, *, session_id:str, device_id:str, device_name:str) -> Claim:
    data = _request('POST', f'{config.get_roasters_url()}/{roaster_id}/claim',
                    json={'sessionId': session_id, 'deviceId': device_id, 'deviceName': device_name})
    try:
        return Claim(
            equipment_id=str(data['equipmentId']),
            outcome=str(data.get('outcome') or 'claimed'),
            version=int(data['version']),
            expires_at=_dt(data.get('leaseExpiresAt')),
            ttl_seconds=int(data['ttlSeconds']),
            heartbeat_seconds=int(data['heartbeatSeconds']),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise RoasterUnavailable('server', 'Phản hồi của ERP không đúng định dạng') from e


def heartbeat(roaster_id:str, *, session_id:str, version:int) -> datetime|None:
    data = _request('POST', f'{config.get_roasters_url()}/{roaster_id}/heartbeat', json={'sessionId': session_id, 'version': version})
    return _dt(data.get('leaseExpiresAt')) if isinstance(data, dict) else None


def release(roaster_id:str, *, session_id:str, version:int) -> None:
    _request('POST', f'{config.get_roasters_url()}/{roaster_id}/release', json={'sessionId': session_id, 'version': version})


def force_release(roaster_id:str, *, reason:str) -> None:
    _request('POST', f'{config.get_roasters_url()}/{roaster_id}/force-release', json={'reason': reason})
