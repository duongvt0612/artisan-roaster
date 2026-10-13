#!/usr/bin/python
#
# device.py
#
# ABOUT
# Identity of this Artisan installation towards the ERP roaster lease:
#   - device_id: stable per Windows PC (stored locally), lets the same PC take its roaster back at once after a crash
#   - session_id: new for every run of Artisan, identifies the lease holder
#   - the roaster chosen last time, to preselect it

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
import platform
import socket
import uuid
from typing import Any, Final, Protocol

from erp import config

_log: Final[logging.Logger] = logging.getLogger(__name__)

KEY_DEVICE_ID: Final[str] = 'deviceId'
KEY_LAST_ROASTER: Final[str] = 'lastRoasterId'
MAX_NAME_LEN: Final[int] = 100

_SESSION_ID: Final[str] = str(uuid.uuid4())
_fallback_device_id:str|None = None # only used if the settings store is unusable


class Store(Protocol):
    """The QSettings calls used here."""
    def value(self, key:str, defaultValue:Any = None) -> Any: ... # noqa: N803
    def setValue(self, key:str, value:Any) -> None: ... # noqa: N802


def _default_store() -> Store:
    # a store of its own (not Artisan's settings): Artisan's RESET must not forget which PC this is
    from PyQt6.QtCore import QSettings
    return QSettings('LVG', 'ArtisanErp')


def device_id(store:Store|None = None) -> str:
    global _fallback_device_id # pylint: disable=global-statement
    try:
        st = store if store is not None else _default_store()
        stored = st.value(KEY_DEVICE_ID, '')
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
        fresh = str(uuid.uuid4())
        st.setValue(KEY_DEVICE_ID, fresh)
        return fresh
    except Exception as e: # pylint: disable=broad-except
        _log.info('device id store not available: %s', type(e).__name__)
        if _fallback_device_id is None:
            _fallback_device_id = str(uuid.uuid4())
        return _fallback_device_id


def session_id() -> str:
    return _SESSION_ID


def device_name() -> str:
    name = ''
    for source in (socket.gethostname, platform.node):
        try:
            name = (source() or '').strip()
        except Exception: # pylint: disable=broad-except
            name = ''
        if name:
            break
    return (name or 'Artisan PC')[:MAX_NAME_LEN]


def last_roaster_key() -> str:
    """The settings key of the roaster chosen last time: one per environment (a roaster of staging means nothing on production)."""
    try:
        env = config.environment()
    except config.ConfigError:
        env = 'unconfigured'
    return f'{KEY_LAST_ROASTER}.{env}'


def last_roaster_id(store:Store|None = None) -> str|None:
    try:
        st = store if store is not None else _default_store()
        value = st.value(last_roaster_key(), '')
        return value.strip() if isinstance(value, str) and value.strip() else None
    except Exception as e: # pylint: disable=broad-except
        _log.info('settings store not available: %s', type(e).__name__)
        return None


def set_last_roaster_id(roaster_id:str|None, store:Store|None = None) -> None:
    try:
        st = store if store is not None else _default_store()
        st.setValue(last_roaster_key(), roaster_id or '')
    except Exception as e: # pylint: disable=broad-except
        _log.info('settings store not available: %s', type(e).__name__)
