#!/usr/bin/python
#
# live_stream.py
#
# ABOUT
# Sends the samples of the roast being recorded to the ERP while it is recorded, so that the ERP can draw the curve live
# (event `roast:samples` on the realtime channel, answered by an ack). Only for a recording that belongs to an ERP batch.
#
#   * The GUI thread hands over new samples with offer(): that only copies them into a locked mailbox, it never talks to the network.
#   * A background thread sends what the ERP has not confirmed yet, at most MAX_BATCH samples per packet. The ERP answers
#     {ok:true, lastSeq} where lastSeq is the last sample it holds WITHOUT gap: the next packet starts right after it, so samples lost
#     on the way (network down, Artisan's connection renewed) are sent again. Sending a sample twice does no harm.
#   * Temporary problems (no connection, no answer, RATE_LIMITED, UNAVAILABLE) are retried after a pause of 1, 2, then 5 s.
#     A final refusal (STOP_CODES) stops the stream for this recording.
#   * After the roast is stopped (end()) what is left is sent for TAIL_SECONDS at most, the last packet says `ended`.
#   * RESET of a cancelled batch (abort()) drops everything at once.
#
# The `.alog` file sent after the batch stays the reference: this stream is a convenience for watching the roast, so every problem
# here is only logged and never interrupts the roaster.

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
import math
import threading
import time
from collections.abc import Callable
from typing import Any, Final

from erp.realtime import NotConnected

_log: Final[logging.Logger] = logging.getLogger(__name__)

EVENT: Final[str] = 'roast:samples'
MAX_BATCH: Final[int] = 600          # samples per packet (the ERP refuses more)
TAIL_SECONDS: Final[float] = 600.0   # after the roast stopped, what is left is sent for this long at most (the ERP accepts 10 minutes)
STOP_CODES: Final[frozenset[str]] = frozenset({'NOT_LEASE_HOLDER', 'ROUND_NOT_ACTIVE', 'FORBIDDEN', 'BAD_PAYLOAD'})
RETRY_PAUSES: Final[tuple[float, ...]] = (1.0, 2.0, 5.0)  # after the 1st, 2nd and any further failure in a row
MAX_SILENT_TIMEOUTS: Final[int] = 3  # an ERP that never answered after this many tries does not know the event: give up
NEXT_PACKET_PAUSE: Final[float] = 0.3  # between packets while catching up (the ERP takes 4 packets/s)
IDLE_WAIT: Final[float] = 5.0         # nothing to send: wait for a new sample (offer() wakes the thread at once)

# the states shown by the banner
IDLE: Final[str] = 'idle'                # no recording is streamed
SENDING: Final[str] = 'sending'          # the ERP gets the samples as they come
CATCHING_UP: Final[str] = 'catching_up'  # the last try failed, the samples wait here and are sent once possible
STOPPED: Final[str] = 'stopped'          # given up for this recording (refused, or the ERP does not listen)

# one sample: (seq, time in ms since the recording started, ET, BT, extra temperatures); seq is the index in Artisan's arrays
Sample = tuple[int, int, float|None, float|None, list[float|None]]


def _clean(value:float|None) -> float|None:
    """A temperature as JSON can carry it: NaN and infinities (a failing probe) become null."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if math.isfinite(v) else None


def _encode(sample:Sample) -> list[Any]:
    seq, t_ms, et, bt, extra = sample
    row:list[Any] = [int(seq), int(t_ms), _clean(et), _clean(bt)]
    if extra:
        row.append([_clean(x) for x in extra])
    return row


class _Stream:
    """One recording being streamed. Replaced by begin(), forgotten by abort() or once everything reached the ERP."""

    def __init__(self, round_id:str, session_id:str, recording_epoch:int, settings:dict[str, Any]) -> None:
        self.round_id = round_id
        self.session_id = session_id
        self.recording_epoch = recording_epoch
        self.settings = settings
        self.settings_sent = False
        self.pending:dict[int, Sample] = {}   # offered and not confirmed by the ERP yet
        self.offered = -1                     # the highest seq handed over by the GUI
        self.acked = -1                       # the ERP holds every sample up to this seq
        self.events:dict[str, Any]|None = None
        self.events_sent:dict[str, Any]|None = None
        self.ended_at:float|None = None       # clock() when the roast stopped
        self.ended_acked = False
        self.ever_acked = False
        self.failures = 0                     # in a row
        self.silent_timeouts = 0              # timeouts before the ERP ever answered
        self.wake = threading.Event()         # new samples, end or abort
        self.halt = threading.Event()         # abort or replaced: even a retry pause ends


class LiveStreamer:
    """Streams the samples of the current recording to the ERP from its own thread. All public methods are cheap and safe from any thread."""

    def __init__(
        self,
        *,
        emit:Callable[[str, dict[str, Any]], Any],
        clock:Callable[[], float] = time.monotonic,
        spawn:bool = True,
        on_state:Callable[[str], None]|None = None,
    ) -> None:
        self._emit = emit
        self._clock = clock
        self._spawn = spawn        # False in tests: pump() is called by hand
        self._on_state = on_state  # called from any thread
        self._lock = threading.Lock()
        self._stream:_Stream|None = None
        self._state = IDLE

    @property
    def state(self) -> str:
        return self._state

    @property
    def next_seq(self) -> int|None:
        """The first seq not handed over yet (None: no recording is streamed)."""
        with self._lock:
            s = self._stream
            return None if s is None else s.offered + 1

    def _set_state(self, state:str) -> None:
        if state == self._state:
            return
        self._state = state
        if self._on_state is not None:
            try:
                self._on_state(state)
            except Exception as e: # pylint: disable=broad-except
                _log.exception(e)

    # ---- called from the GUI thread ----

    def begin(self, round_id:str, session_id:str, recording_epoch:int, settings:dict[str, Any]) -> None:
        """A recording of an ERP batch started: from now on its samples are streamed (a previous stream is dropped)."""
        s = _Stream(round_id, session_id, recording_epoch, settings)
        with self._lock:
            previous, self._stream = self._stream, s
        if previous is not None:
            previous.halt.set()
            previous.wake.set()
        self._set_state(SENDING)
        if self._spawn:
            threading.Thread(target=self._run, args=(s,), name='erp-live-stream', daemon=True).start()

    def offer(self, samples:list[Sample], events:dict[str, Any]|None) -> None:
        """New samples (and the current events) of the recording. Samples handed over before are ignored."""
        with self._lock:
            s = self._stream
            if s is None:
                return
            for sample in samples:
                seq = int(sample[0])
                if seq > s.offered:
                    s.pending[seq] = sample
                    s.offered = seq
            if events is not None:
                s.events = events
        s.wake.set()

    def end(self) -> None:
        """The roast stopped: what is left is sent, the last packet tells the ERP the recording is over."""
        with self._lock:
            s = self._stream
            if s is None or s.ended_at is not None:
                return
            s.ended_at = self._clock()
        s.wake.set()

    def abort(self) -> None:
        """The batch was cancelled (RESET): nothing more is sent."""
        with self._lock:
            s, self._stream = self._stream, None
        if s is not None:
            s.halt.set()
            s.wake.set()
        self._set_state(IDLE)

    # ---- the sender ----

    def _run(self, s:_Stream) -> None:
        while True:
            s.wake.clear()
            try:
                step = self._pump(s)
            except Exception as e: # pylint: disable=broad-except
                _log.exception(e)
                step = (RETRY_PAUSES[-1], False)
            if step is None:
                return
            delay, interruptible = step
            (s.wake if interruptible else s.halt).wait(delay)

    def pump(self) -> tuple[float, bool]|None:
        """One step of the sender: sends one packet if there is something to send. Returns how long to wait before the next step and whether
        a new sample may cut that wait short; None when this stream is over."""
        with self._lock:
            s = self._stream
        return None if s is None else self._pump(s)

    def _gone(self, s:_Stream) -> bool:
        return self._stream is not s

    def _finish(self, s:_Stream, state:str) -> None:
        with self._lock:
            if self._gone(s):
                return
            self._stream = None
        self._set_state(state)

    def _pump(self, s:_Stream) -> tuple[float, bool]|None:
        with self._lock:
            expired = s.ended_at is not None and self._clock() - s.ended_at > TAIL_SECONDS
        if expired:
            _log.info('live roast samples: the tail of batch %s could not be sent in time', s.round_id)
            self._finish(s, STOPPED)
            return None

        with self._lock:
            if self._gone(s):
                return None
            seqs = sorted(seq for seq in s.pending if seq > s.acked)[:MAX_BATCH]
            events_due = s.events is not None and s.events != s.events_sent
            ending = s.ended_at is not None and (not seqs or seqs[-1] >= s.offered)
            if not seqs and not events_due and not (ending and not s.ended_acked):
                return IDLE_WAIT, True
            payload:dict[str, Any] = {'roundId': s.round_id, 'sessionId': s.session_id, 'recordingEpoch': s.recording_epoch,
                                      'samples': [_encode(s.pending[seq]) for seq in seqs]}
            if not s.settings_sent:
                payload['settings'] = s.settings
            if events_due:
                payload['events'] = s.events
            if ending:
                payload['ended'] = True
            sent_events = s.events if events_due else s.events_sent

        try:
            ack = self._emit(EVENT, payload)
        except NotConnected:
            return self._failed(s, timeout=False)
        except TimeoutError:
            return self._failed(s, timeout=True)

        if not isinstance(ack, dict):
            return self._failed(s, timeout=False)
        if ack.get('ok') is not True:
            code = str(ack.get('code') or '')
            if code in STOP_CODES:
                _log.info('live roast samples of batch %s refused by the ERP: %s %s', s.round_id, code, ack.get('message'))
                self._finish(s, STOPPED)
                return None
            return self._failed(s, timeout=False)
        last = ack.get('lastSeq')
        if not isinstance(last, int) or isinstance(last, bool):
            return self._failed(s, timeout=False)

        with self._lock:
            if self._gone(s):
                return None # aborted or replaced while the packet was on its way
            s.acked = max(s.acked, last)
            for seq in [seq for seq in s.pending if seq <= s.acked]:
                del s.pending[seq]
            s.settings_sent = True
            s.events_sent = sent_events
            s.ever_acked = True
            s.failures = 0
            if payload.get('ended') and s.acked >= s.offered:
                s.ended_acked = True
            done = s.ended_acked and not s.pending and s.events == s.events_sent
            more = any(seq > s.acked for seq in s.pending)
        if done:
            self._finish(s, IDLE)
            return None
        self._set_state(SENDING)
        return (NEXT_PACKET_PAUSE, False) if more else (IDLE_WAIT, True)

    def _failed(self, s:_Stream, *, timeout:bool) -> tuple[float, bool]|None:
        with self._lock:
            if self._gone(s):
                return None
            s.failures += 1
            if timeout and not s.ever_acked:
                s.silent_timeouts += 1
            silent = s.silent_timeouts >= MAX_SILENT_TIMEOUTS
            pause = RETRY_PAUSES[min(s.failures, len(RETRY_PAUSES)) - 1]
        if silent:
            _log.info('live roast samples: the ERP never answered, streaming of batch %s is given up', s.round_id)
            self._finish(s, STOPPED)
            return None
        self._set_state(CATCHING_UP)
        return pause, False
