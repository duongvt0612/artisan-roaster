#!/usr/bin/python
#
# lease.py
#
# ABOUT
# Keeps the claimed roaster alive: a background heartbeat renews the ERP lease and tells when it is gone.
#
#   HELD     the ERP confirmed the lease (or nothing indicates otherwise)
#   SUSPECT  no heartbeat got through for most of the lease time: the ERP may hand the roaster to someone else soon
#   LOST     the ERP says this session does not hold the roaster any more (terminal; claim again to get a new manager)
#   IDLE     stopped
#
# Pure Python (listeners are called from the heartbeat thread): the Qt side wraps it, see erp.banner.
# Losing the lease NEVER touches data being recorded locally, it only tells the rest of Artisan to stop controlling the roaster.

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

import enum
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Final

from erp import config, roasters

_log: Final[logging.Logger] = logging.getLogger(__name__)


def session_reason(reason:str|None) -> str:
    """The reason of a lease lost because the login session ended: 'session_expired' if it only expired, 'session_<code>' if the ERP ended it."""
    return f'session_{reason}' if reason else 'session_expired'


class LeaseState(enum.Enum):
    IDLE = 'idle'
    HELD = 'held'
    SUSPECT = 'suspect'
    LOST = 'lost'


Listener = Callable[[LeaseState, str], None]


class LeaseManager:

    def __init__(
        self,
        roaster_id:str,
        roaster_name:str,
        claim:Any,
        *,
        session_id:str,
        api:Any = roasters,
        clock:Callable[[], float] = time.monotonic,
        interval:float|None = None,
    ) -> None:
        self.roaster_id:str = roaster_id
        self.roaster_name:str = roaster_name
        self.version:int = claim.version
        self._session_id:str = session_id
        self._api:Any = api
        self._clock:Callable[[], float] = clock
        self._interval:float = interval if interval is not None else max(1.0, float(claim.heartbeat_seconds))
        self._suspect_after:float = claim.ttl_seconds * config.lease_suspect_fraction
        self._state:LeaseState = LeaseState.HELD
        self._last_ok:float = clock()
        self._listeners:list[Listener] = []
        self._lock:threading.Lock = threading.Lock()
        self._stop:threading.Event = threading.Event()
        self._thread:threading.Thread|None = None

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def is_ok(self) -> bool:
        return self._state is LeaseState.HELD

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def add_listener(self, listener:Listener) -> None:
        """listener(state, reason) is called from the heartbeat thread whenever the state changes."""
        self._listeners.append(listener)

    def _set_state(self, state:LeaseState, reason:str) -> None:
        with self._lock:
            if self._state is state:
                return
            self._state = state
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(state, reason)
            except Exception as e: # pylint: disable=broad-except
                _log.exception(e)

    def _lose(self, reason:str) -> None:
        _log.info('roaster lease lost: %s', reason)
        self._set_state(LeaseState.LOST, reason)

    def tick(self) -> bool:
        """One heartbeat. Returns False once the lease is over (nothing more to renew)."""
        if self._state not in (LeaseState.HELD, LeaseState.SUSPECT) or self._stop.is_set():
            return False
        try:
            self._api.heartbeat(self.roaster_id, session_id=self._session_id, version=self.version)
        except roasters.LeaseLost:
            return self._definitive('lease_lost')
        except roasters.SessionExpired as e:
            return self._definitive(session_reason(e.reason))
        except roasters.RoasterNotFound:
            return self._definitive('not_found')
        except roasters.RoasterForbidden:
            return self._definitive('forbidden')
        except roasters.RoasterUnavailable:
            self._unreachable()
            return True
        except roasters.RoasterError as e:
            _log.info('roaster heartbeat refused: %s', e.message)
            return True
        except Exception as e: # pylint: disable=broad-except
            _log.exception(e)
            return True
        self._last_ok = self._clock()
        if self._state is LeaseState.SUSPECT:
            self._set_state(LeaseState.HELD, 'recovered')
        return True

    def _definitive(self, reason:str) -> bool:
        if not self._stop.is_set():
            self._lose(reason)
        return False

    def _unreachable(self) -> None:
        if self._state is LeaseState.HELD and self._clock() - self._last_ok >= self._suspect_after and not self._stop.is_set():
            self._set_state(LeaseState.SUSPECT, 'no_connection')

    def notify_session_revoked(self, reason:str) -> None:
        """The ERP told over the realtime channel that our login session was ended: the heartbeat could not renew the lease anyway."""
        if self._state in (LeaseState.HELD, LeaseState.SUSPECT) and not self._stop.is_set():
            self._lose(session_reason(reason))

    def notify_lease_lost(self, roaster_id:str, reason:str) -> None:
        """The ERP told us over the realtime channel (e.g. a forced release): no need to wait for the next heartbeat."""
        if roaster_id == self.roaster_id and self._state in (LeaseState.HELD, LeaseState.SUSPECT) and not self._stop.is_set():
            self._lose(reason)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name='erp-lease-heartbeat', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if not self.tick():
                break

    def stop(self, release:bool = True) -> None:
        """Stops the heartbeat and, if the roaster is still ours, gives it back. Never raises, never blocks for long."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._lock:
            previous = self._state
            self._state = LeaseState.IDLE
        if release and previous in (LeaseState.HELD, LeaseState.SUSPECT):
            try:
                self._api.release(self.roaster_id, session_id=self._session_id, version=self.version)
            except Exception as e: # pylint: disable=broad-except
                # the lease simply expires on its own after a few seconds
                _log.info('could not release the roaster: %s', type(e).__name__)
