#!/usr/bin/python
#
# client.py
#
# ABOUT
# HTTP client for the authentication endpoints of the LVG ERP:
#   POST /auth/login    {username, password}
#   POST /auth/refresh  {refreshToken}
#   POST /auth/logout   (Bearer)
# All replies are wrapped as {success, errors, errorMessages, data}.

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
from typing import Any, Final, Literal

import requests

from erp import config

_log: Final[logging.Logger] = logging.getLogger(__name__)

ErrorKind = Literal['invalid_credentials', 'disabled', 'invalid_session', 'rate_limited', 'network', 'server']


class ErpAuthError(Exception):
    """Raised if the ERP rejects or cannot answer an authentication request."""

    def __init__(self, kind:ErrorKind, message:str) -> None:
        super().__init__(message)
        self.kind:ErrorKind = kind
        self.message:str = message


@dataclass(frozen=True)
class AuthResult:
    access_token:str
    refresh_token:str
    user:dict[str, Any]


def _json(r:requests.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_message(body:dict[str, Any], default:str) -> str:
    messages = body.get('errorMessages')
    if isinstance(messages, list) and messages and isinstance(messages[0], str) and messages[0]:
        return messages[0]
    return default


def _is_disabled_message(message:str) -> bool:
    # the ERP gives no error code, only a Vietnamese message ("Tài khoản đã bị khóa ...")
    lowered = message.lower()
    return 'khóa' in lowered or 'khoá' in lowered


def _post(url:str, payload:dict[str, Any], headers:dict[str, str]|None = None) -> requests.Response:
    try:
        return requests.post(url, json=payload, headers=headers, timeout=config.timeout)
    except requests.exceptions.RequestException as e:
        _log.info('ERP request failed: %s', type(e).__name__)
        raise ErpAuthError('network', str(e)) from e


def _check(r:requests.Response, rejected:ErrorKind) -> dict[str, Any]:
    """Returns the "data" part of a successful reply, raises ErpAuthError otherwise."""
    body = _json(r)
    if r.status_code == 429:
        raise ErpAuthError('rate_limited', _error_message(body, 'Too many requests'))
    if r.status_code in {400, 401, 403}:
        message = _error_message(body, 'Authentication failed')
        kind:ErrorKind = 'disabled' if rejected == 'invalid_credentials' and _is_disabled_message(message) else rejected
        raise ErpAuthError(kind, message)
    if r.status_code >= 500 or not 200 <= r.status_code < 300:
        raise ErpAuthError('server', _error_message(body, f'HTTP {r.status_code}'))
    data = body.get('data')
    if body.get('success') is False or not isinstance(data, dict):
        raise ErpAuthError('server', _error_message(body, 'Unexpected reply from the ERP'))
    return data


def login(username:str, password:str) -> AuthResult:
    _log.debug('ERP login()')
    data = _check(_post(config.get_login_url(), {'username': username, 'password': password}), 'invalid_credentials')
    access_token = data.get('accessToken')
    refresh_token = data.get('refreshToken')
    user = data.get('user')
    if not isinstance(access_token, str) or not isinstance(refresh_token, str) or not isinstance(user, dict):
        raise ErpAuthError('server', 'Unexpected reply from the ERP')
    return AuthResult(access_token, refresh_token, user)


def refresh(refresh_token:str) -> AuthResult:
    _log.debug('ERP refresh()')
    data = _check(_post(config.get_refresh_url(), {'refreshToken': refresh_token}), 'invalid_session')
    access_token = data.get('accessToken')
    user = data.get('user')
    if not isinstance(access_token, str) or not isinstance(user, dict):
        raise ErpAuthError('server', 'Unexpected reply from the ERP')
    # the ERP does not rotate refresh tokens, but accept a new one if it ever sends one
    new_refresh_token = data.get('refreshToken')
    return AuthResult(access_token, new_refresh_token if isinstance(new_refresh_token, str) else refresh_token, user)


def logout(access_token:str) -> bool:
    """Best effort: returns True if the ERP confirmed the logout. Revokes ALL refresh tokens of the user."""
    _log.debug('ERP logout()')
    try:
        r = _post(config.get_logout_url(), {}, {'Authorization': f'Bearer {access_token}'})
    except ErpAuthError:
        return False
    return 200 <= r.status_code < 300
