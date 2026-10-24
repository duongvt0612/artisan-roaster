"""Unit tests for erp.lease.LeaseManager: keeping a roaster lease alive and noticing when it is gone."""

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from erp import roasters
from erp.lease import LeaseManager, LeaseState


class FakeApi:
    """Stands in for the erp.roasters module."""

    def __init__(self) -> None:
        self.heartbeats:list[tuple[str, str, int]] = []
        self.releases:list[tuple[str, str, int]] = []
        self.heartbeat_error:Exception|None = None
        self.release_error:Exception|None = None
        self.beat_event = threading.Event()

    def heartbeat(self, roaster_id:str, *, session_id:str, version:int) -> None:
        self.heartbeats.append((roaster_id, session_id, version))
        self.beat_event.set()
        if self.heartbeat_error is not None:
            raise self.heartbeat_error

    def release(self, roaster_id:str, *, session_id:str, version:int) -> None:
        self.releases.append((roaster_id, session_id, version))
        if self.release_error is not None:
            raise self.release_error


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _claim(ttl:int = 30, beat:int = 10, version:int = 4) -> Any:
    return SimpleNamespace(version=version, ttl_seconds=ttl, heartbeat_seconds=beat)


def _manager(api:FakeApi|None = None, clock:Clock|None = None, **kw:Any) -> tuple[LeaseManager, FakeApi, Clock, list[tuple[LeaseState, str]]]:
    api = api or FakeApi()
    clock = clock or Clock()
    m = LeaseManager('eq1', 'Máy Rang Nhỏ', _claim(**kw), session_id='s1', api=api, clock=clock)
    events:list[tuple[LeaseState, str]] = []
    m.add_listener(lambda state, reason: events.append((state, reason)))
    return m, api, clock, events


def test_starts_held_with_the_claimed_version() -> None:
    m, _api, _clock, _events = _manager()
    assert m.state is LeaseState.HELD and m.is_ok
    assert (m.roaster_id, m.roaster_name, m.version) == ('eq1', 'Máy Rang Nhỏ', 4)


def test_heartbeat_uses_session_and_version_and_stays_quiet() -> None:
    m, api, _clock, events = _manager()
    assert m.tick() is True
    assert api.heartbeats == [('eq1', 's1', 4)]
    assert events == [] and m.state is LeaseState.HELD


def test_lease_lost_is_terminal_and_reported_once() -> None:
    m, api, _clock, events = _manager()
    api.heartbeat_error = roasters.LeaseLost('bị chiếm')
    assert m.tick() is False
    assert m.state is LeaseState.LOST and not m.is_ok
    assert events == [(LeaseState.LOST, 'lease_lost')]
    # a later heartbeat must not resurrect it, nor report again
    api.heartbeat_error = None
    assert m.tick() is False
    assert events == [(LeaseState.LOST, 'lease_lost')]
    assert len(api.heartbeats) == 1


@pytest.mark.parametrize(('error', 'reason'), [
    (roasters.SessionExpired('hết hạn'), 'session_expired'),
    (roasters.SessionExpired('kick', 'kicked_admin'), 'session_kicked_admin'),
    (roasters.SessionExpired('kick', ''), 'session_expired'),
    (roasters.RoasterNotFound('không có'), 'not_found'),
    (roasters.RoasterForbidden('không quyền'), 'forbidden'),
])
def test_other_definitive_answers_also_end_the_lease(error:Exception, reason:str) -> None:
    m, api, _clock, events = _manager()
    api.heartbeat_error = error
    assert m.tick() is False
    assert events == [(LeaseState.LOST, reason)]


def test_session_revoked_over_realtime_ends_the_lease_at_once() -> None:
    m, _api, _clock, events = _manager()
    m.notify_session_revoked('password_changed')
    assert m.state is LeaseState.LOST
    assert events == [(LeaseState.LOST, 'session_password_changed')]
    m.notify_session_revoked('password_changed')  # reported once
    assert len(events) == 1


def test_network_trouble_turns_suspect_only_after_most_of_the_ttl_and_recovers() -> None:
    m, api, clock, events = _manager(ttl=30, beat=10)
    api.heartbeat_error = roasters.RoasterUnavailable('network', 'down')
    clock.now += 10
    assert m.tick() is True and m.state is LeaseState.HELD     # 10s without answer: still fine
    clock.now += 10
    assert m.tick() is True and m.state is LeaseState.HELD     # 20s < 24s (80% of 30s)
    clock.now += 10
    assert m.tick() is True and m.state is LeaseState.SUSPECT  # 30s: the ERP may have given the roaster away
    clock.now += 10
    m.tick()
    assert events == [(LeaseState.SUSPECT, 'no_connection')]   # reported once, not on every failed beat
    api.heartbeat_error = None
    assert m.tick() is True
    assert m.state is LeaseState.HELD
    assert events[-1] == (LeaseState.HELD, 'recovered')


def test_after_the_network_returns_a_lost_lease_is_discovered() -> None:
    m, api, clock, events = _manager(ttl=30, beat=10)
    api.heartbeat_error = roasters.RoasterUnavailable('network', 'down')
    clock.now += 40
    m.tick()
    assert m.state is LeaseState.SUSPECT
    api.heartbeat_error = roasters.LeaseLost('đã bị chiếm')
    assert m.tick() is False
    assert events[-1] == (LeaseState.LOST, 'lease_lost')


def test_unexpected_errors_do_not_kill_the_loop() -> None:
    m, api, _clock, events = _manager()
    api.heartbeat_error = roasters.RoasterError('lạ')
    assert m.tick() is True and events == []
    api.heartbeat_error = RuntimeError('bug')
    assert m.tick() is True and events == []


def test_realtime_lease_lost_for_this_roaster_ends_it_at_once() -> None:
    m, _api, _clock, events = _manager()
    m.notify_lease_lost('someone-else', 'force_released')
    assert m.state is LeaseState.HELD and events == []
    m.notify_lease_lost('eq1', 'force_released')
    assert m.state is LeaseState.LOST
    assert events == [(LeaseState.LOST, 'force_released')]


def test_stop_releases_the_roaster_with_the_current_version() -> None:
    m, api, _clock, _events = _manager()
    m.stop()
    assert api.releases == [('eq1', 's1', 4)]
    assert m.state is LeaseState.IDLE
    m.stop()  # idempotent: no second release
    assert len(api.releases) == 1


def test_stop_without_release_or_when_already_lost_does_not_call_the_erp() -> None:
    m, api, _clock, _events = _manager()
    m.stop(release=False)
    assert api.releases == []
    m2, api2, _c, _e = _manager()
    api2.heartbeat_error = roasters.LeaseLost('x')
    m2.tick()
    m2.stop()
    assert api2.releases == []


def test_stop_swallows_release_errors() -> None:
    m, api, _clock, _events = _manager()
    api.release_error = roasters.RoasterUnavailable('network', 'down')
    m.stop()  # quitting Artisan must never hang or crash on a dead network
    api.release_error = RuntimeError('bug')
    m2, api2, _c, _e = _manager()
    api2.release_error = RuntimeError('bug')
    m2.stop()


def test_a_failing_listener_does_not_break_the_manager() -> None:
    m, api, _clock, events = _manager()
    m.add_listener(lambda *_: (_ for _ in ()).throw(RuntimeError('ui bug')))
    api.heartbeat_error = roasters.LeaseLost('x')
    assert m.tick() is False
    assert events == [(LeaseState.LOST, 'lease_lost')]


def test_background_thread_beats_and_stops() -> None:
    api = FakeApi()
    m = LeaseManager('eq1', 'Máy', _claim(), session_id='s1', api=api, interval=0.02)
    m.start()
    assert api.beat_event.wait(2), 'no heartbeat was sent'
    time.sleep(0.1)
    m.stop()
    sent = len(api.heartbeats)
    assert sent >= 2
    time.sleep(0.1)
    assert len(api.heartbeats) == sent, 'heartbeats must stop after stop()'
    assert not m.is_running


def test_background_thread_stops_itself_when_the_lease_is_lost() -> None:
    api = FakeApi()
    api.heartbeat_error = roasters.LeaseLost('x')
    m = LeaseManager('eq1', 'Máy', _claim(), session_id='s1', api=api, interval=0.02)
    lost = threading.Event()
    m.add_listener(lambda state, _r: lost.set() if state is LeaseState.LOST else None)
    m.start()
    assert lost.wait(2)
    time.sleep(0.1)
    assert not m.is_running
    assert len(api.heartbeats) == 1
