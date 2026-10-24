#!/usr/bin/python
#
# realtime.py
#
# ABOUT
# Listens to the ERP Socket.IO gateway for roaster events, so that the roaster list and the lease banner react at once:
#   roaster:changed     {equipmentId, status, version}   a roaster was claimed / released (room "roasters")
#   roaster:lease-lost  {equipmentId, reason}            this user lost a roaster, e.g. forced release (own room)
#
# This is ONLY an accelerator. The lease itself lives in the ERP database and is kept alive over HTTP (erp.lease), so Artisan stays
# correct without this channel: the selection dialog polls and the heartbeat discovers a lost lease. If python-socketio is not
# installed or the gateway is unreachable, this class just keeps retrying quietly.
#
# Reconnecting is done here (not by python-socketio) because the gateway only checks the access token when it connects, and that
# token lives 15 minutes: every attempt starts with a freshly renewed one.

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
import threading
from collections.abc import Callable
from typing import Any, Final

_log: Final[logging.Logger] = logging.getLogger(__name__)

ROOM_ROASTERS: Final[str] = 'roasters'
EVENT_CHANGED: Final[str] = 'roaster:changed'
EVENT_LEASE_LOST: Final[str] = 'roaster:lease-lost'
EVENT_SESSION_REVOKED: Final[str] = 'auth:session-revoked' # the login session was ended; the server drops the socket right after it

Payload = dict[str, Any]


def is_available() -> bool:
    try:
        import socketio # type:ignore[import-untyped,unused-ignore] # noqa: F401 # pylint: disable=import-outside-toplevel,unused-import
    except ImportError:
        return False
    return True


def _default_factory(**kwargs:Any) -> Any:
    import socketio # type:ignore[import-untyped,unused-ignore] # pylint: disable=import-outside-toplevel
    return socketio.Client(**kwargs)


class RealtimeClient:

    def __init__(
        self,
        *,
        url:str,
        get_token:Callable[[], str|None],
        on_changed:Callable[[Payload], None],
        on_lease_lost:Callable[[Payload], None],
        on_session_revoked:Callable[[str], None] = lambda _reason: None,
        client_factory:Callable[..., Any] = _default_factory,
        backoff:tuple[float, float] = (5.0, 60.0),
        wait:Callable[[float], bool]|None = None,
    ) -> None:
        self._url:str = url
        self._get_token:Callable[[], str|None] = get_token
        self._on_changed:Callable[[Payload], None] = on_changed
        self._on_lease_lost:Callable[[Payload], None] = on_lease_lost
        self._on_session_revoked:Callable[[str], None] = on_session_revoked
        self._factory:Callable[..., Any] = client_factory
        self._backoff:tuple[float, float] = backoff
        self._stop:threading.Event = threading.Event()
        self._wait:Callable[[float], bool] = wait if wait is not None else self._stop.wait
        self._thread:threading.Thread|None = None
        self._sio:Any = None
        self._lock:threading.Lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name='erp-realtime', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            sio = self._sio
        if sio is not None:
            try:
                sio.disconnect()
            except Exception as e: # pylint: disable=broad-except
                _log.debug('realtime disconnect: %s', type(e).__name__)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _dispatch(self, callback:Callable[[Payload], None], payload:Any) -> None:
        if not isinstance(payload, dict):
            return
        try:
            callback(payload)
        except Exception as e: # pylint: disable=broad-except
            _log.exception(e)

    def _revoked(self, payload:Any) -> None:
        """The ERP ended our login session: there is nothing to reconnect with, so the channel stops for good.

        The reason may be empty (the periodic check of the server does not know it)."""
        reason = payload.get('reason') if isinstance(payload, dict) else None
        self._stop.set() # first: the disconnect that follows must not start another attempt
        try:
            self._on_session_revoked(reason if isinstance(reason, str) else '')
        except Exception as e: # pylint: disable=broad-except
            _log.exception(e)

    def _run(self) -> None:
        base, cap = self._backoff
        delay = base
        while not self._stop.is_set():
            connected = False
            try:
                token = self._get_token()
                if token:
                    connected = self._session(token)
            except Exception as e: # pylint: disable=broad-except
                _log.info('realtime channel not available: %s', type(e).__name__)
            if self._stop.is_set():
                break
            if connected:
                delay = base # it worked and then dropped: come back soon
            if self._wait(delay):
                break
            if not connected:
                delay = min(delay * 2, cap) # failed attempts back off up to the cap

    def _session(self, token:str) -> bool:
        """One connection from connect to disconnect. Returns True if it got connected."""
        sio = self._factory(reconnection=False, logger=False, engineio_logger=False)
        sio.on(EVENT_CHANGED, lambda payload=None: self._dispatch(self._on_changed, payload))
        sio.on(EVENT_LEASE_LOST, lambda payload=None: self._dispatch(self._on_lease_lost, payload))
        sio.on(EVENT_SESSION_REVOKED, lambda payload=None: self._revoked(payload))
        sio.on('error', lambda payload=None: _log.info('realtime gateway error: %s', payload))
        with self._lock:
            self._sio = sio
        sio.connect(self._url, auth={'token': token}, transports=['websocket'], wait_timeout=10)
        sio.emit('join-room', ROOM_ROASTERS)
        sio.wait()
        return True
