"""Unit tests for erp.realtime: the Socket.IO listener for roaster events (a fake socket stands in for python-socketio)."""

import threading
from typing import Any

import pytest

from erp import realtime
from erp.realtime import RealtimeClient


class FakeSio:
    """Records what the client does with python-socketio's Client API."""

    instances:list['FakeSio'] = []

    def __init__(self, **kwargs:Any) -> None:
        self.kwargs = kwargs
        self.handlers:dict[str, Any] = {}
        self.emitted:list[tuple[str, Any]] = []
        self.connect_args:tuple[Any, ...] = ()
        self.connect_kwargs:dict[str, Any] = {}
        self.disconnected = False
        self.connect_error:Exception|None = None
        self.hold = threading.Event()  # wait() returns when set, like a closed connection
        FakeSio.instances.append(self)

    def on(self, event:str, handler:Any = None) -> Any:
        if handler is None:
            def deco(fn:Any) -> Any:
                self.handlers[event] = fn
                return fn
            return deco
        self.handlers[event] = handler
        return handler

    def connect(self, *args:Any, **kwargs:Any) -> None:
        self.connect_args, self.connect_kwargs = args, kwargs
        if self.connect_error is not None:
            raise self.connect_error

    def emit(self, event:str, data:Any = None) -> None:
        self.emitted.append((event, data))

    def wait(self) -> None:
        self.hold.wait(5)

    def disconnect(self) -> None:
        self.disconnected = True
        self.hold.set()


@pytest.fixture(autouse=True)
def _reset() -> None:
    FakeSio.instances = []


class Harness:
    def __init__(self, tokens:list[str|None]|None = None) -> None:
        self.tokens = list(tokens) if tokens is not None else ['tok1']
        self.changed:list[dict[str, Any]] = []
        self.lost:list[dict[str, Any]] = []
        self.revoked:list[str] = []
        self.waits:list[float] = []
        self.connected = threading.Event()

    def get_token(self) -> str|None:
        return self.tokens.pop(0) if len(self.tokens) > 1 else (self.tokens[0] if self.tokens else None)

    def make(self, **kw:Any) -> RealtimeClient:
        return RealtimeClient(
            url='http://localhost:10301',
            get_token=self.get_token,
            on_changed=self.changed.append,
            on_lease_lost=self.lost.append,
            on_session_revoked=self.revoked.append,
            client_factory=FakeSio,
            **kw,
        )


def _wait_for(predicate:Any, timeout:float = 2.0) -> bool:
    end = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        end.wait(0.01)
    return predicate()


def test_connects_with_the_token_over_websocket_and_joins_the_roasters_room() -> None:
    h = Harness()
    c = h.make()
    c.start()
    assert _wait_for(lambda: FakeSio.instances and FakeSio.instances[0].emitted)
    sio = FakeSio.instances[0]
    assert sio.connect_args == ('http://localhost:10301',)
    assert sio.connect_kwargs['auth'] == {'token': 'tok1'}
    assert sio.connect_kwargs['transports'] == ['websocket']
    assert ('join-room', 'roasters') in sio.emitted
    assert sio.kwargs.get('reconnection') is False  # reconnecting is ours, it needs a fresh token each time
    c.stop()
    assert sio.disconnected


def test_dispatches_roaster_events() -> None:
    h = Harness()
    c = h.make()
    c.start()
    assert _wait_for(lambda: FakeSio.instances and 'roaster:changed' in FakeSio.instances[0].handlers)
    sio = FakeSio.instances[0]
    sio.handlers['roaster:changed']({'equipmentId': 'eq1', 'status': 'occupied', 'version': 3})
    sio.handlers['roaster:lease-lost']({'equipmentId': 'eq1', 'reason': 'force_released'})
    assert h.changed == [{'equipmentId': 'eq1', 'status': 'occupied', 'version': 3}]
    assert h.lost == [{'equipmentId': 'eq1', 'reason': 'force_released'}]
    c.stop()


def test_bad_payloads_and_failing_callbacks_do_not_break_the_listener() -> None:
    h = Harness()
    c = RealtimeClient(url='http://x', get_token=h.get_token, client_factory=FakeSio,
                       on_changed=lambda _p: (_ for _ in ()).throw(RuntimeError('ui bug')),
                       on_lease_lost=h.lost.append)
    c.start()
    assert _wait_for(lambda: FakeSio.instances and 'roaster:changed' in FakeSio.instances[0].handlers)
    sio = FakeSio.instances[0]
    sio.handlers['roaster:changed']({'equipmentId': 'eq1'})
    sio.handlers['roaster:changed']('not a dict')
    sio.handlers['roaster:lease-lost']('not a dict')
    sio.handlers['roaster:lease-lost'](None)
    assert h.lost == []
    c.stop()


def test_reconnects_with_a_fresh_token_after_the_connection_drops() -> None:
    h = Harness(tokens=['tok1', 'tok2'])
    c = h.make(backoff=(0.01, 0.02))
    c.start()
    assert _wait_for(lambda: FakeSio.instances and FakeSio.instances[0].emitted)
    FakeSio.instances[0].hold.set()  # the server went away
    assert _wait_for(lambda: len(FakeSio.instances) >= 2 and FakeSio.instances[1].emitted)
    assert FakeSio.instances[1].connect_kwargs['auth'] == {'token': 'tok2'}
    c.stop()


def test_connection_errors_are_retried_with_growing_pauses() -> None:
    h = Harness()
    delays:list[float] = []
    c = RealtimeClient(url='http://x', get_token=h.get_token, on_changed=h.changed.append, on_lease_lost=h.lost.append,
                       client_factory=lambda **kw: _failing(kw), backoff=(0.01, 0.04), wait=lambda s: delays.append(s) or False)
    c.start()
    assert _wait_for(lambda: len(delays) >= 4)
    c.stop()
    assert delays[0] == 0.01
    assert delays[1] > delays[0]
    assert max(delays) <= 0.04  # capped


def _failing(kw:dict[str, Any]) -> FakeSio:
    sio = FakeSio(**kw)
    sio.connect_error = ConnectionError('refused')
    return sio


def test_without_a_token_it_waits_instead_of_connecting() -> None:
    h = Harness(tokens=[None])
    c = h.make(backoff=(0.01, 0.02))
    c.start()
    threading.Event().wait(0.1)
    assert FakeSio.instances == []
    c.stop()


def test_stop_is_idempotent_and_safe_before_start() -> None:
    h = Harness()
    c = h.make()
    c.stop()
    c.start()
    c.stop()
    c.stop()
    assert not c.is_running


def test_availability_reflects_whether_python_socketio_is_importable() -> None:
    assert isinstance(realtime.is_available(), bool)


def test_session_revoked_is_reported_and_the_channel_stops_for_good() -> None:
    h = Harness()
    c = h.make(backoff=(0.01, 0.02))
    c.start()
    assert _wait_for(lambda: FakeSio.instances and 'auth:session-revoked' in FakeSio.instances[0].handlers)
    sio = FakeSio.instances[0]
    sio.handlers['auth:session-revoked']({'reason': 'kicked_admin'})
    sio.hold.set()  # the server drops the socket right after the event
    assert _wait_for(lambda: not c.is_running)
    assert h.revoked == ['kicked_admin']
    assert len(FakeSio.instances) == 1  # no reconnect with a dead session


@pytest.mark.parametrize('payload', [None, {}, {'reason': ''}, {'reason': 5}, 'x'])
def test_session_revoked_without_a_usable_reason_is_reported_with_an_empty_one(payload:Any) -> None:
    h = Harness()
    c = h.make()
    c.start()
    assert _wait_for(lambda: FakeSio.instances and 'auth:session-revoked' in FakeSio.instances[0].handlers)
    FakeSio.instances[0].handlers['auth:session-revoked'](payload)
    assert h.revoked == ['']
    c.stop()


def test_a_failing_revoked_callback_does_not_break_the_channel() -> None:
    def boom(_reason:str) -> None:
        raise RuntimeError('ui bug')
    c = RealtimeClient(url='http://x', get_token=lambda: 'tok', client_factory=FakeSio, on_changed=lambda _p: None,
                       on_lease_lost=lambda _p: None, on_session_revoked=boom)
    c.start()
    assert _wait_for(lambda: FakeSio.instances and 'auth:session-revoked' in FakeSio.instances[0].handlers)
    FakeSio.instances[0].handlers['auth:session-revoked']({'reason': 'logout'})
    c.stop()
