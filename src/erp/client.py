#!/usr/bin/python
#
# client.py
#
# ABOUT
# HTTP client for the authentication endpoints of the LVG ERP:
#   POST /auth/login    {username, password}
#   POST /auth/refresh  {refreshToken}   (the ERP rotates it: every reply carries a NEW refresh token)
#   POST /artisan/logout {sessionId}  (Bearer): Artisan's own logout, see logout()
# Every request says who it is with the headers X-Client-Type and X-Device-Name, see client_headers().
# All replies are wrapped as {success, errors, errorMessages, data}.
# A login session that the ERP ended (kicked, password changed, ...) answers 401 with errors[0] == 'SESSION_REVOKED', see revoked_reason().

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

from erp import config, device

_log: Final[logging.Logger] = logging.getLogger(__name__)

ErrorKind = Literal['invalid_credentials', 'disabled', 'invalid_session', 'rate_limited', 'network', 'server']


CLIENT_TYPE:Final[str] = 'artisan'
SESSION_REVOKED:Final[str] = 'SESSION_REVOKED'

# what to tell the user if the ERP ended the login session of this Artisan (errors[1] of a SESSION_REVOKED reply)
_REVOKED_TEXT:Final[dict[str, str]] = {
    'kicked_self': 'Phiên này đã bị đăng xuất từ một thiết bị khác của bạn.',
    'kicked_admin': 'Phiên đăng nhập đã bị quản trị viên kết thúc.',
    'password_changed': 'Mật khẩu đã được đổi, vui lòng đăng nhập lại.',
    'password_reset': 'Mật khẩu đã được quản trị viên đặt lại.',
    'user_disabled': 'Tài khoản đã bị khoá, liên hệ quản trị viên.',
    'reuse_detected': 'Phát hiện bất thường, đã đăng xuất để bảo vệ tài khoản.',
}
_REVOKED_DEFAULT:Final[str] = 'Phiên đăng nhập đã kết thúc, vui lòng đăng nhập lại.'


def revoked_text(reason:str) -> str:
    return _REVOKED_TEXT.get(reason, _REVOKED_DEFAULT)


class ErpAuthError(Exception):
    """Raised if the ERP rejects or cannot answer an authentication request.

    `reason` is only set if the ERP ended the login session on purpose (kind 'invalid_session' after SESSION_REVOKED): its code, e.g. 'kicked_admin'
    (an empty string if the ERP gave none)."""

    def __init__(self, kind:ErrorKind, message:str, reason:str|None = None) -> None:
        super().__init__(message)
        self.kind:ErrorKind = kind
        self.message:str = message
        self.reason:str|None = reason


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


def revoked_reason(body:Any) -> str|None:
    """The reason code if `body` (a parsed reply) says the login session was ended by the ERP ('' if no reason is given), None if it does not."""
    if not isinstance(body, dict):
        return None
    errors = body.get('errors')
    if not isinstance(errors, list) or not errors or errors[0] != SESSION_REVOKED:
        return None
    return errors[1] if len(errors) > 1 and isinstance(errors[1], str) else ''


def client_headers() -> dict[str, str]:
    """X-Client-Type is mandatory on login / refresh (400 without it), the device name is what the user sees in the list of his sessions."""
    # HTTP headers are latin-1 at best: the name of a PC may contain anything
    name = device.device_name().encode('ascii', 'replace').decode('ascii')
    return {'X-Client-Type': CLIENT_TYPE, 'X-Device-Name': name}


def _is_disabled_message(message:str) -> bool:
    # the ERP gives no error code, only a Vietnamese message ("Tài khoản đã bị khóa ...")
    lowered = message.lower()
    return 'khóa' in lowered or 'khoá' in lowered


def _post(url:str, payload:dict[str, Any], headers:dict[str, str]|None = None) -> requests.Response:
    try:
        return requests.post(url, json=payload, headers={**client_headers(), **(headers or {})}, timeout=config.timeout)
    except requests.exceptions.RequestException as e:
        _log.info('ERP request failed: %s', type(e).__name__)
        raise ErpAuthError('network', str(e)) from e


def _check(r:requests.Response, rejected:ErrorKind) -> dict[str, Any]:
    """Returns the "data" part of a successful reply, raises ErpAuthError otherwise."""
    body = _json(r)
    if r.status_code == 429:
        raise ErpAuthError('rate_limited', _error_message(body, 'Too many requests'))
    if r.status_code in {400, 401, 403}:
        reason = revoked_reason(body) if r.status_code == 401 else None
        if reason is not None:
            raise ErpAuthError('invalid_session', _error_message(body, revoked_text(reason)), reason)
        message = _error_message(body, 'Authentication failed')
        kind:ErrorKind = 'disabled' if rejected == 'invalid_credentials' and _is_disabled_message(message) else rejected
        raise ErpAuthError(kind, message)
    if r.status_code >= 500 or not 200 <= r.status_code < 300:
        raise ErpAuthError('server', _error_message(body, f'HTTP {r.status_code}'))
    data = body.get('data')
    if body.get('success') is False or not isinstance(data, dict):
        raise ErpAuthError('server', _error_message(body, 'Unexpected reply from the ERP'))
    return data


def request(method:str, url:str, token:str, json:Any = None, params:dict[str, Any]|None = None,
            data:dict[str, Any]|None = None, files:dict[str, Any]|None = None) -> requests.Response:
    """Authenticated request (Bearer). Network problems become ErpAuthError('network'); the status code is the caller's business.

    `data` + `files` make a multipart upload: `files` values must be bytes tuples (name, content, type), not open files, so that the
    request can be sent again after the access token was renewed."""
    try:
        return requests.request(method, url, json=json, params=params, data=data, files=files, headers={**client_headers(), 'Authorization': f'Bearer {token}'},
                                timeout=config.upload_timeout if files else config.timeout)
    except requests.exceptions.RequestException as e:
        _log.info('ERP request failed: %s', type(e).__name__)
        raise ErpAuthError('network', str(e)) from e


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
    # the ERP rotates the refresh token on every refresh: the old one is worth nothing after 30 seconds, so a reply without a new one is unusable
    new_refresh_token = data.get('refreshToken')
    if not isinstance(access_token, str) or not isinstance(new_refresh_token, str) or not new_refresh_token or not isinstance(user, dict):
        raise ErpAuthError('server', 'Unexpected reply from the ERP')
    return AuthResult(access_token, new_refresh_token, user)


def logout(access_token:str, *, session_id:str) -> bool:
    """Artisan's own logout (best effort): the ERP gives back the roaster this session holds and ends THIS login session only
    (it knows which one from the access token; a refreshToken in the body would be refused with 400).

    `session_id` is the lease session of this run of Artisan (device.session_id()), not the id of the login session.
    The user stays logged in on his other devices (web, mobile). Returns True if the ERP confirmed the logout."""
    _log.debug('ERP logout()')
    try:
        r = _post(config.get_logout_url(), {'sessionId': session_id}, {'Authorization': f'Bearer {access_token}'})
    except ErpAuthError:
        return False
    return 200 <= r.status_code < 300
