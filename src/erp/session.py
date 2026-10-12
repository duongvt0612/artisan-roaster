#!/usr/bin/python
#
# session.py
#
# ABOUT
# Holds the ERP session of the running application. The access token only lives in memory.
# If "remember" is ticked, the refresh token (and the user name, for prefilling the login
# dialog) is kept in the keyring of the operating system. If no keyring is available the
# session still works, the user just has to log in on every start.

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

import base64
import json
import logging
import threading
import time
from typing import Any, Final

import requests

from erp import client, config

_log: Final[logging.Logger] = logging.getLogger(__name__)

_REFRESH_TOKEN_KEY: Final[str] = 'refresh_token'
_USERNAME_KEY: Final[str] = 'username'


def _keyring() -> Any:
    import keyring # imported late, as in plus.connection, to keep packaging happy
    return keyring


def _load(key:str) -> str|None:
    try:
        return _keyring().get_password(config.app_name, key) # type:ignore[no-any-return]
    except Exception as e: # pylint: disable=broad-except
        _log.info('keyring not available: %s', type(e).__name__)
        return None


def _store(key:str, value:str) -> None:
    try:
        _keyring().set_password(config.app_name, key, value)
    except Exception as e: # pylint: disable=broad-except
        _log.info('keyring not available: %s', type(e).__name__)


def _delete(key:str) -> None:
    try:
        _keyring().delete_password(config.app_name, key)
    except Exception as e: # pylint: disable=broad-except
        # also raised if there is nothing stored under this key
        _log.debug('keyring delete failed: %s', type(e).__name__)


_RENEW_MARGIN: Final[float] = 60.0


def _seconds_left(token:str) -> float:
    """Seconds until the JWT expires (not verified, only read); an unreadable token counts as expired."""
    try:
        payload = token.split('.')[1]
        exp = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['exp']
        return float(exp) - time.time()
    except Exception: # pylint: disable=broad-except
        return 0.0


class Session:

    __slots__ = ['access_token', 'refresh_token', 'user', '_lock']

    def __init__(self) -> None:
        self.access_token:str|None = None
        # kept in memory even if "remember" is off: the access token only lives 15 minutes, a long session must be able to renew it
        self.refresh_token:str|None = None
        self.user:dict[str, Any]|None = None
        self._lock:threading.Lock = threading.Lock()

    @property
    def is_authenticated(self) -> bool:
        return self.access_token is not None

    def stored_username(self) -> str|None:
        return _load(_USERNAME_KEY)

    def _set(self, result:client.AuthResult) -> None:
        self.access_token = result.access_token
        self.refresh_token = result.refresh_token
        self.user = result.user

    def login(self, username:str, password:str, remember:bool) -> dict[str, Any]:
        """Raises client.ErpAuthError on failure."""
        result = client.login(username, password)
        self._set(result)
        if remember:
            _store(_REFRESH_TOKEN_KEY, result.refresh_token)
            _store(_USERNAME_KEY, username)
        else:
            _delete(_REFRESH_TOKEN_KEY)
            _delete(_USERNAME_KEY)
        return result.user

    def restore(self) -> bool:
        """Re-opens the session from the stored refresh token.

        Returns False if there is nothing (valid) to restore. Raises client.ErpAuthError
        if the ERP could not be asked (network, server error, rate limit); the stored
        token is kept in this case.
        """
        refresh_token = _load(_REFRESH_TOKEN_KEY)
        if not refresh_token:
            return False
        try:
            result = client.refresh(refresh_token)
        except client.ErpAuthError as e:
            if e.kind == 'invalid_session':
                _log.info('stored ERP session is not valid anymore')
                self.clear(keep_username=True)
                return False
            raise
        self._set(result)
        if result.refresh_token != refresh_token:
            _store(_REFRESH_TOKEN_KEY, result.refresh_token)
        return True

    def clear(self, keep_username:bool = False) -> None:
        self.access_token = None
        self.refresh_token = None
        self.user = None
        _delete(_REFRESH_TOKEN_KEY)
        if not keep_username:
            _delete(_USERNAME_KEY)

    def authed_request(self, method:str, url:str, *, json:Any = None, params:dict[str, Any]|None = None) -> requests.Response:
        """Bearer request that renews the access token once on a 401 and retries.

        Raises client.ErpAuthError: 'invalid_session' if not logged in or the refresh token is dead (the session is dropped),
        'network' / 'server' / 'rate_limited' if the ERP could not be reached (the session is kept).
        """
        token = self.access_token
        if token is None:
            raise client.ErpAuthError('invalid_session', 'Chưa đăng nhập ERP')
        response = client.request(method, url, token, json=json, params=params)
        if response.status_code != 401:
            return response
        self._renew_access_token(stale=token)
        renewed = self.access_token
        if renewed is None:
            raise client.ErpAuthError('invalid_session', 'Phiên đăng nhập ERP đã hết hạn')
        return client.request(method, url, renewed, json=json, params=params)

    def fresh_access_token(self) -> str|None:
        """The access token, renewed first if it expires within a minute. For the realtime channel, which only checks the token when it connects."""
        token = self.access_token
        if token is None:
            return None
        if _seconds_left(token) > _RENEW_MARGIN:
            return token
        try:
            self._renew_access_token(stale=token)
        except client.ErpAuthError as e:
            if e.kind == 'invalid_session':
                return None
            return token # ERP unreachable: let the caller try, the server refuses an expired token anyway
        return self.access_token

    def _renew_access_token(self, stale:str) -> None:
        with self._lock:
            if self.access_token is not None and self.access_token != stale:
                return # another thread renewed it while we waited for the lock
            refresh_token = self.refresh_token
            if not refresh_token:
                self.clear(keep_username=True)
                raise client.ErpAuthError('invalid_session', 'Phiên đăng nhập ERP đã hết hạn')
            try:
                result = client.refresh(refresh_token)
            except client.ErpAuthError as e:
                if e.kind == 'invalid_session':
                    _log.info('ERP session expired, logging out')
                    self.clear(keep_username=True)
                raise
            self._set(result)
            if result.refresh_token != refresh_token and _load(_REFRESH_TOKEN_KEY):
                _store(_REFRESH_TOKEN_KEY, result.refresh_token)

    def logout(self) -> None:
        """Ends the session on the ERP (best effort) and forgets everything stored locally.

        NOTE: the ERP revokes ALL refresh tokens of the user, i.e. other devices are logged out as well.
        """
        if self.access_token is not None:
            client.logout(self.access_token)
        self.clear()


session:Final[Session] = Session()
