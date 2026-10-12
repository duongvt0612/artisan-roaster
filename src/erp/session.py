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

import logging
from typing import Any, Final

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


class Session:

    __slots__ = ['access_token', 'user']

    def __init__(self) -> None:
        self.access_token:str|None = None
        self.user:dict[str, Any]|None = None

    @property
    def is_authenticated(self) -> bool:
        return self.access_token is not None

    def stored_username(self) -> str|None:
        return _load(_USERNAME_KEY)

    def _set(self, result:client.AuthResult) -> None:
        self.access_token = result.access_token
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
        self.user = None
        _delete(_REFRESH_TOKEN_KEY)
        if not keep_username:
            _delete(_USERNAME_KEY)

    def logout(self) -> None:
        """Ends the session on the ERP (best effort) and forgets everything stored locally.

        NOTE: the ERP revokes ALL refresh tokens of the user, i.e. other devices are logged out as well.
        """
        if self.access_token is not None:
            client.logout(self.access_token)
        self.clear()


session:Final[Session] = Session()
