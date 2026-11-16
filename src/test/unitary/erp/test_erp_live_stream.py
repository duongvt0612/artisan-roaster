"""Unit tests for erp.live_stream: the background sender of the live roast samples (the ERP is a scripted emit, time a fake clock).

Most tests drive the sender one step at a time (`spawn=False` + `pump()`), so nothing depends on thread timing."""

import math
import threading
from typing import Any

import pytest

from erp import live_stream
from erp.live_stream import LiveStreamer, Sample
from erp.realtime import NotConnected


def samples(first:int, last:int, extra:bool = False) -> list[Sample]:
    return [(i, i * 3000, 200.0 + i, 150.0 + i, [50.0 + i, None] if extra else []) for i in range(first, last + 1)]


class Erp:
    """Answers each emit from a script: a dict is the ack, an exception is raised; an empty script acks everything sent."""

    def __init__(self, *script:Any) -> None:
        self.script = list(script)
        self.sent:list[dict[str, Any]] = []
        self.threads:list[str] = []

    def __call__(self, event:str, payload:dict[str, Any]) -> Any:
        assert event == 'roast:samples'
        self.sent.append(payload)
        self.threads.append(threading.current_thread().name)
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step
        last = payload['samples'][-1][0] if payload['samples'] else -1
        return {'ok': True, 'lastSeq': last}

    def seqs(self, i:int) -> list[int]:
        return [s[0] for s in self.sent[i]['samples']]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def streamer(erp:Erp, clock:Clock|None = None, states:list[str]|None = None) -> LiveStreamer:
    s = LiveStreamer(emit=erp, clock=clock or Clock(), spawn=False, on_state=states.append if states is not None else None)
    s.begin('r1', 'sess-1', 1763270000, {'deltaBTspan': 20, 'mode': 'C'})
    return s


def test_the_first_packet_carries_the_batch_the_session_and_the_settings() -> None:
    erp = Erp()
    s = streamer(erp)
    s.offer(samples(0, 0) + samples(1, 2, extra=True), {'timeindex': [0, 0, 0, 0, 0, 0, 0, 0]})
    s.pump()
    p = erp.sent[0]
    assert p['roundId'] == 'r1' and p['sessionId'] == 'sess-1' and p['recordingEpoch'] == 1763270000
    assert p['settings'] == {'deltaBTspan': 20, 'mode': 'C'}
    assert p['events'] == {'timeindex': [0, 0, 0, 0, 0, 0, 0, 0]}
    assert p['samples'][0] == [0, 0, 200.0, 150.0]  # no extra devices: no fifth element
    assert p['samples'][1] == [1, 3000, 201.0, 151.0, [51.0, None]]
    assert 'ended' not in p
    s.offer(samples(3, 3), {'timeindex': [0, 0, 0, 0, 0, 0, 0, 0]})
    s.pump()
    assert 'settings' not in erp.sent[1]  # settings once per recording
    assert 'events' not in erp.sent[1]    # events only when they changed


def test_changed_events_are_sent_again() -> None:
    erp = Erp()
    s = streamer(erp)
    s.offer(samples(0, 0), {'timeindex': [0, 0, 0, 0, 0, 0, 0, 0]})
    s.pump()
    s.offer(samples(1, 1), {'timeindex': [1, 0, 0, 0, 0, 0, 0, 0]})
    s.pump()
    assert erp.sent[1]['events'] == {'timeindex': [1, 0, 0, 0, 0, 0, 0, 0]}


def test_offer_never_talks_to_the_erp_and_ignores_samples_already_offered() -> None:
    erp = Erp()
    s = streamer(erp)
    s.offer(samples(0, 4), None)
    s.offer(samples(0, 5), None)  # the GUI may hand over overlapping ranges
    assert erp.sent == []
    assert s.next_seq == 6
    s.pump()
    assert erp.seqs(0) == [0, 1, 2, 3, 4, 5]


def test_packets_are_cut_at_600_samples_in_order() -> None:
    erp = Erp()
    s = streamer(erp)
    s.offer(samples(0, 1299), None)
    for _ in range(3):
        s.pump()
    assert [len(p['samples']) for p in erp.sent] == [600, 600, 100]
    assert erp.seqs(1)[0] == 600 and erp.seqs(2)[-1] == 1299


def test_the_next_packet_starts_after_what_the_erp_has_without_gap() -> None:
    erp = Erp({'ok': True, 'lastSeq': 2})  # 3..5 did not make it (a gap on the ERP side)
    s = streamer(erp)
    s.offer(samples(0, 5), None)
    s.pump()
    s.pump()
    assert erp.seqs(1) == [3, 4, 5]
    assert s.state == 'sending'


def test_not_connected_and_timeouts_are_retried_with_growing_pauses_without_losing_samples() -> None:
    states:list[str] = []
    erp = Erp(NotConnected(), TimeoutError(), NotConnected(), NotConnected())
    s = streamer(erp, states=states)
    s.offer(samples(0, 1), None)
    pauses = [s.pump() for _ in range(4)]
    assert [p[0] for p in pauses if p] == [1.0, 2.0, 5.0, 5.0]
    assert all(p is not None and p[1] is False for p in pauses)  # a new sample must not cut the pause short
    assert s.state == 'catching_up'
    s.offer(samples(2, 2), None)
    s.pump()
    assert erp.seqs(4) == [0, 1, 2]
    assert s.state == 'sending'
    assert states[:2] == ['sending', 'catching_up'] and states[-1] == 'sending'


@pytest.mark.parametrize('code', ['RATE_LIMITED', 'UNAVAILABLE'])
def test_temporary_refusals_are_retried(code:str) -> None:
    erp = Erp({'ok': False, 'code': code, 'message': 'x'})
    s = streamer(erp)
    s.offer(samples(0, 1), None)
    assert s.pump() == (1.0, False)
    s.pump()
    assert erp.seqs(1) == [0, 1]


@pytest.mark.parametrize('code', ['NOT_LEASE_HOLDER', 'ROUND_NOT_ACTIVE', 'FORBIDDEN', 'BAD_PAYLOAD'])
def test_final_refusals_stop_the_stream(code:str) -> None:
    erp = Erp({'ok': False, 'code': code, 'message': 'x'})
    s = streamer(erp)
    s.offer(samples(0, 1), None)
    assert s.pump() is None
    assert s.state == 'stopped'
    s.offer(samples(2, 2), None)
    assert s.pump() is None
    assert len(erp.sent) == 1


def test_three_timeouts_before_any_ack_mean_the_erp_does_not_listen() -> None:
    erp = Erp(TimeoutError(), TimeoutError(), TimeoutError())
    s = streamer(erp)
    s.offer(samples(0, 0), None)
    assert s.pump() is not None
    assert s.pump() is not None
    assert s.pump() is None
    assert s.state == 'stopped'


def test_timeouts_after_an_ack_keep_retrying() -> None:
    erp = Erp({'ok': True, 'lastSeq': 0}, TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError())
    s = streamer(erp)
    s.offer(samples(0, 0), None)
    s.pump()
    s.offer(samples(1, 1), None)
    for _ in range(4):
        assert s.pump() is not None
    assert s.state == 'catching_up'


def test_a_strange_answer_is_retried() -> None:
    erp = Erp('nonsense', {'ok': True, 'lastSeq': 'x'})
    s = streamer(erp)
    s.offer(samples(0, 0), None)
    assert s.pump() == (1.0, False)
    assert s.pump() == (2.0, False)
    s.pump()
    assert erp.seqs(2) == [0]


def test_end_marks_the_last_packet_and_finishes_once_acked() -> None:
    states:list[str] = []
    erp = Erp()
    s = streamer(erp, states=states)
    s.offer(samples(0, 1), None)
    s.pump()
    s.offer(samples(2, 2), None)
    s.end()
    assert s.pump() is None
    assert erp.sent[-1]['ended'] is True and erp.seqs(1) == [2]
    assert s.state == 'idle' and states[-1] == 'idle'


def test_end_with_nothing_new_still_tells_the_erp() -> None:
    erp = Erp()
    s = streamer(erp)
    s.offer(samples(0, 1), None)
    s.pump()
    s.end()
    s.pump()
    assert erp.sent[-1] == {'roundId': 'r1', 'sessionId': 'sess-1', 'recordingEpoch': 1763270000, 'samples': [], 'ended': True}


def test_after_end_the_tail_is_sent_for_ten_minutes_at_most() -> None:
    clock = Clock()
    erp = Erp(*[NotConnected()] * 50)
    s = streamer(erp, clock)
    s.offer(samples(0, 1), None)
    s.end()
    assert s.pump() is not None
    clock.now += live_stream.TAIL_SECONDS + 1
    assert s.pump() is None
    assert s.state == 'stopped'
    assert len(erp.sent) == 1  # gave up without another try


def test_abort_drops_everything_and_sends_nothing_more() -> None:
    erp = Erp()
    s = streamer(erp)
    s.offer(samples(0, 3), None)
    s.abort()
    assert s.pump() is None
    assert erp.sent == []
    assert s.state == 'idle' and s.next_seq is None
    s.offer(samples(4, 4), None)  # a late sample of the GUI is ignored
    assert s.pump() is None


def test_an_ack_arriving_after_abort_changes_nothing() -> None:
    s:LiveStreamer|None = None

    def emit(_event:str, payload:dict[str, Any]) -> Any:
        assert s is not None
        s.abort()  # RESET while the packet was on its way
        return {'ok': True, 'lastSeq': payload['samples'][-1][0]}

    s = LiveStreamer(emit=emit, clock=Clock(), spawn=False)
    s.begin('r1', 'sess-1', 1, {})
    s.offer(samples(0, 0), None)
    assert s.pump() is None
    assert s.state == 'idle'


def test_a_new_recording_replaces_the_previous_one() -> None:
    erp = Erp()
    s = streamer(erp)
    s.offer(samples(0, 3), None)
    s.begin('r1', 'sess-1', 1763279999, {'mode': 'C'})  # taken over after a crash: seq restarts at 0
    assert s.next_seq == 0
    s.offer(samples(0, 0), None)
    s.pump()
    assert erp.sent[0]['recordingEpoch'] == 1763279999 and erp.seqs(0) == [0]


def test_values_json_cannot_carry_become_null() -> None:
    erp = Erp()
    s = streamer(erp)
    s.offer([(0, 0, math.nan, math.inf, [-math.inf, 12.345678])], None)
    s.pump()
    assert erp.sent[0]['samples'][0] == [0, 0, None, None, [None, 12.35]]


def test_without_a_stream_everything_is_a_no_op() -> None:
    erp = Erp()
    s = LiveStreamer(emit=erp, clock=Clock(), spawn=False)
    s.offer(samples(0, 1), None)
    s.end()
    s.abort()
    assert s.pump() is None
    assert s.state == 'idle' and s.next_seq is None
    assert erp.sent == []


def test_the_thread_sends_in_the_background() -> None:
    erp = Erp()
    s = LiveStreamer(emit=erp)
    s.begin('r1', 'sess-1', 1, {})
    s.offer(samples(0, 2), None)  # called from this (GUI) thread: the packet leaves from the sender's own thread
    s.end()
    done = threading.Event()
    for _ in range(300):
        if s.state == 'idle':
            done.set()
            break
        threading.Event().wait(0.01)
    assert done.is_set()
    assert erp.seqs(0) == [0, 1, 2]
    assert erp.threads and all(name == 'erp-live-stream' for name in erp.threads)
