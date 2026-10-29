"""Unit tests for erp.roast_flow: what happens around a roast batch between START and the weight reaching the ERP.

The dialogs and the message boxes are replaced by scripted doubles, the ERP API by a fake: nothing here shows a window or talks to a server."""

import os
import time
from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from erp import background, batch as erp_batch, roast_flow, roasters, rounds, weigh_dialog  # noqa: E402,F401
from erp.rounds import ActiveRound, FinishedRound, RoastCommand, RoundInput, StartedRound  # noqa: E402


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


def command() -> RoastCommand:
    return RoastCommand('c1', 'LS-1026-000123', 'normal', 'pending', 1, 'Arabica rang', 'TP', 'kg', 120, 40, 80, 'YC-1', (),
                        (RoundInput('g1', 'NX-A', 'Nhân A', 'kg', 55.5),))


def started() -> StartedRound:
    return StartedRound('r1', 3, 'c1', 1, 32.0, 15.0, datetime(2026, 10, 13, 8, 0, tzinfo=UTC))


def a_batch(**kw:Any) -> erp_batch.Batch:
    base = {'round_id': 'r1', 'round_number': 3, 'command_id': 'c1', 'command_code': 'LS-1026-000123', 'kind': 'normal',
            'output_name': 'Arabica rang', 'output_unit': 'kg', 'input_kg': 40.0, 'expected_output_kg': 32.0, 'tolerance_percent': 15.0,
            'started_at': '2026-10-13T08:00:00+00:00', 'equipment_id': 'eq1'}
    base.update(kw)
    return erp_batch.Batch(**base)


def active(*, mine:bool = True, **kw:Any) -> ActiveRound:
    base = {'round_id': 'r9', 'round_number': 2, 'command_id': 'c1', 'command_code': 'LS-1', 'kind': 'normal', 'step_number': 1,
            'output_name': 'Arabica rang', 'output_unit': 'kg', 'target_kg': 100.0, 'expected_output_kg': 8.5, 'tolerance_percent': 12.0,
            'started_at': datetime(2026, 10, 13, 7, 15, tzinfo=UTC), 'started_by_id': 'u1', 'started_by_name': 'Thợ rang',
            'from_artisan': True, 'mine': mine, 'inputs': (RoundInput('g1', 'NX-A', 'Nhân A', 'kg', 10.0),)}
    base.update(kw)
    return ActiveRound(**base)


class FakeAw:
    """The few things of Artisan's main window the flow touches."""

    def __init__(self, unit:str = 'Kg') -> None:
        self.qmc = SimpleNamespace(weight=(0.0, 0.0, unit), title='', beans='', flagstart=False, timeindex=[0]*8, roastepoch=1000, ground_color=0)
        self.messages:list[str] = []

    def sendmessage(self, text:str) -> None:
        self.messages.append(text)


class FakeStartDialog:
    """Scripted StartRoundDialog: script = list of dicts with the outcome of each time it is shown."""
    script:list[dict[str, Any]] = []
    created:list['FakeStartDialog'] = []

    def __init__(self, _parent:Any, _aw:Any, **kw:Any) -> None:
        self.kw = kw
        step = FakeStartDialog.script.pop(0)
        self.accepted = bool(step.get('accept', False))
        self.started = step.get('started')
        self.command = step.get('command')
        self.input_lines = step.get('lines', [])
        self.need_login = bool(step.get('need_login', False))
        self.busy_with_round = bool(step.get('busy', False))
        FakeStartDialog.created.append(self)

    def exec(self) -> int:
        return 1 if self.accepted else 0

    def deleteLater(self) -> None:  # noqa: N802
        pass


class FakeFinishDialog:
    script:list[dict[str, Any]] = []
    created:list['FakeFinishDialog'] = []

    def __init__(self, _parent:Any, _aw:Any, *, batch:Any, powder_color:str = '', **_kw:Any) -> None:
        self.batch, self.powder_color = batch, powder_color
        step = FakeFinishDialog.script.pop(0)
        self.result_round = step.get('finished')
        self.already_over = bool(step.get('over', False))
        self.need_login = bool(step.get('need_login', False))
        FakeFinishDialog.created.append(self)

    def exec(self) -> int:
        return 1

    def deleteLater(self) -> None:  # noqa: N802
        pass


class FakeWeighDialog:
    """Scripted WeighPendingDialog: what is left to weigh when it closes, and whether the login expired meanwhile."""
    script:list[dict[str, Any]] = []
    created:list['FakeWeighDialog'] = []

    def __init__(self, _parent:Any, _aw:Any, **kw:Any) -> None:
        self.kw = kw
        step = FakeWeighDialog.script.pop(0)
        self.remaining = step.get('remaining')
        self.need_login = bool(step.get('need_login', False))
        FakeWeighDialog.created.append(self)

    def exec(self) -> int:
        return 0

    def deleteLater(self) -> None:  # noqa: N802
        pass


class Env(SimpleNamespace):
    infos:list[tuple[str, str]]
    choices:list[int]
    asked:list[tuple[str, list[str]]]
    reasons:list[str|None]
    api:MagicMock
    changed:MagicMock


@pytest.fixture
def env(qapp:QApplication) -> Generator[Env, None, None]:  # noqa: ARG001
    roast_flow._reset_state()  # pylint: disable=protected-access
    FakeStartDialog.script, FakeStartDialog.created = [], []
    FakeFinishDialog.script, FakeFinishDialog.created = [], []
    FakeWeighDialog.script, FakeWeighDialog.created = [], []
    e = Env(infos=[], choices=[], asked=[], reasons=[], api=MagicMock(), changed=MagicMock())

    def info(_aw:Any, text:str, detail:str = '') -> None:
        e.infos.append((text, detail))

    def choose(_aw:Any, text:str, detail:str, labels:list[str], default:int = 0) -> int:  # noqa: ARG001
        e.asked.append((text, labels))
        return e.choices.pop(0)

    def reason(_aw:Any) -> str|None:
        return e.reasons.pop(0)

    e.api.active_round.return_value = None
    e.api.list_awaiting_weigh.return_value = []
    with patch.object(roast_flow.start_dialog, 'StartRoundDialog', FakeStartDialog), \
         patch.object(roast_flow.weigh_dialog, 'WeighPendingDialog', FakeWeighDialog), \
         patch.object(roast_flow.finish_dialog, 'FinishRoundDialog', FakeFinishDialog), \
         patch.object(roast_flow, '_info', info), patch.object(roast_flow, '_choose', choose), patch.object(roast_flow, '_ask_reason', reason), \
         patch.object(roast_flow, 'rounds', e.api):
        e.api.RoundNotRunning = rounds.RoundNotRunning
        roast_flow.set_listener(e.changed)
        yield e
    roast_flow._reset_state()  # pylint: disable=protected-access


def settle(qapp:QApplication, timeout:float = 3.0) -> None:
    """Lets the answers of the calls made in the background arrive and be handled."""
    end = time.monotonic() + timeout
    while not background.idle() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()


class Ticker:
    """Counts the turns of the event loop: a frozen GUI would not have any while it waits for the ERP."""

    def __init__(self) -> None:
        self.ticks = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(10)

    def _tick(self) -> None:
        self.ticks += 1

    def stop(self) -> None:
        self.timer.stop()


def slowly(seconds:float, value:Any = None) -> Any:
    def call(*_a:Any, **_kw:Any) -> Any:
        time.sleep(seconds)
        return value
    return call


def lease(ok:bool = True) -> MagicMock:
    m = MagicMock()
    m.is_ok = ok
    m.roaster_id = 'eq1'
    m.roaster_name = 'Máy Rang Nhỏ (ROAST-01)'
    m.version = 7
    return m


def start(aw:FakeAw, lse:MagicMock|None = None, *, capacity:float|None = 15.0, relogin:Any = None) -> bool:
    return roast_flow.before_start(aw, lse if lse is not None else lease(), capacity_kg=capacity, relogin=relogin or (lambda: True))  # type:ignore[arg-type]


def accepted_start() -> dict[str, Any]:
    return {'accept': True, 'started': started(), 'command': command(), 'lines': [(RoundInput('g1', 'NX-A', 'Nhân A', 'kg', 55.5), 40.0)]}


class TestBeforeStart:
    def test_without_a_roaster_nothing_starts(self, env:Env) -> None:
        assert start(FakeAw(), lease(ok=False)) is False
        assert 'chưa giữ máy rang' in env.infos[0][0]
        assert FakeStartDialog.created == []

    def test_a_started_batch_lets_artisan_record_and_fills_its_properties(self, env:Env) -> None:
        FakeStartDialog.script = [accepted_start()]
        aw = FakeAw()
        assert start(aw) is True
        b = roast_flow.current_batch()
        assert b is not None and (b.round_id, b.round_number, b.command_code, b.input_kg, b.expected_output_kg, b.tolerance_percent) == ('r1', 3, 'LS-1026-000123', 40.0, 32.0, 15.0)
        assert aw.qmc.weight == (40.0, 0.0, 'Kg')       # Weight In = what he loaded, Weight Out is still unknown
        assert aw.qmc.title == 'Arabica rang' and aw.qmc.beans == 'Nhân A'
        env.changed.assert_called()
        kw = FakeStartDialog.created[0].kw
        assert (kw['roaster_name'], kw['equipment_id'], kw['version'], kw['capacity_kg']) == ('Máy Rang Nhỏ (ROAST-01)', 'eq1', 7, 15.0)

    def test_weight_follows_the_unit_artisan_uses(self, env:Env) -> None:  # noqa: ARG002
        FakeStartDialog.script = [accepted_start()]
        aw = FakeAw(unit='g')
        assert start(aw) is True
        assert aw.qmc.weight == (40000.0, 0.0, 'g')

    def test_closing_the_dialog_does_not_start(self, env:Env) -> None:  # noqa: ARG002
        FakeStartDialog.script = [{'accept': False}]
        aw = FakeAw()
        assert start(aw) is False
        assert roast_flow.current_batch() is None and aw.qmc.weight == (0.0, 0.0, 'Kg')

    def test_an_expired_login_asks_to_log_in_and_does_not_start(self, env:Env) -> None:  # noqa: ARG002
        FakeStartDialog.script = [{'need_login': True}]
        relogin = MagicMock(return_value=True)
        assert start(FakeAw(), relogin=relogin) is False
        relogin.assert_called_once()

    def test_a_previous_unfinished_batch_has_to_be_finished_first(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'finished': FinishedRound('r1', 3, 30.0, 32.0, 25.0, False, 15.0)}]
        FakeStartDialog.script = [accepted_start()]
        assert start(FakeAw()) is True
        assert 'chưa được kết thúc' in env.infos[0][0] and len(FakeFinishDialog.created) == 1
        b = roast_flow.current_batch()
        assert b is not None and b.round_id == 'r1' and b.input_kg == 40.0  # the new batch

    def test_if_the_previous_batch_is_not_finished_nothing_starts(self, env:Env) -> None:  # noqa: ARG002
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{}]  # he closes the finish dialog
        assert start(FakeAw()) is False
        assert FakeStartDialog.created == []

    def test_a_batch_of_somebody_else_blocks_the_roaster(self, env:Env) -> None:
        FakeStartDialog.script = [{'busy': True}]
        env.api.active_round.return_value = active(mine=False)
        assert start(FakeAw()) is False
        assert 'của người khác' in env.infos[0][0] and 'Thợ rang' in env.infos[0][1] and 'LS-1' in env.infos[0][1]
        # and the next START asks the ERP again; the batch is still there
        FakeStartDialog.script = []
        assert start(FakeAw()) is False
        assert len(env.infos) == 2

    def test_a_foreign_batch_that_ended_meanwhile_no_longer_blocks(self, env:Env) -> None:
        roast_flow._flow.foreign = active(mine=False)  # pylint: disable=protected-access
        env.api.active_round.return_value = None
        FakeStartDialog.script = [accepted_start()]
        assert start(FakeAw()) is True

    def test_my_own_running_batch_is_offered_for_finishing(self, env:Env) -> None:
        FakeStartDialog.script = [{'busy': True}]
        env.api.active_round.return_value = active(mine=True)
        env.choices = [1]  # later
        assert start(FakeAw()) is False
        b = roast_flow.current_batch()
        assert b is not None and b.round_id == 'r9' and b.input_kg == 10.0


class TestNoFreeze:
    def test_checking_a_foreign_batch_keeps_the_event_loop_running(self, env:Env) -> None:
        roast_flow._flow.foreign = active(mine=False)  # pylint: disable=protected-access
        env.api.active_round.side_effect = slowly(0.4, None)
        FakeStartDialog.script = [accepted_start()]
        ticker = Ticker()
        try:
            assert start(FakeAw()) is True
        finally:
            ticker.stop()
        assert ticker.ticks >= 10

    def test_finding_out_whose_batch_blocks_the_roaster_keeps_the_event_loop_running(self, env:Env) -> None:
        FakeStartDialog.script = [{'busy': True}]
        env.api.active_round.side_effect = slowly(0.4, active(mine=False))
        ticker = Ticker()
        try:
            assert start(FakeAw()) is False
        finally:
            ticker.stop()
        assert ticker.ticks >= 10 and 'của người khác' in env.infos[0][0]

    def test_giving_up_the_foreign_check_does_not_start_and_does_not_complain(self, env:Env) -> None:
        roast_flow._flow.foreign = active(mine=False)  # pylint: disable=protected-access
        with patch.object(roast_flow.background, 'call_with_wait', side_effect=background.Cancelled()):
            assert start(FakeAw()) is False
        assert env.infos == [] and FakeStartDialog.created == []

    def test_giving_up_finding_out_who_blocks_the_roaster_does_not_complain(self, env:Env) -> None:
        FakeStartDialog.script = [{'busy': True}]
        with patch.object(roast_flow.background, 'call_with_wait', side_effect=background.Cancelled()):
            assert start(FakeAw()) is False
        assert env.infos == []

    def test_cancelling_a_batch_keeps_the_event_loop_running(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        env.choices, env.reasons = [1], ['rang hỏng']
        env.api.cancel_round.side_effect = slowly(0.4)
        ticker = Ticker()
        try:
            assert roast_flow.before_reset(FakeAw()) is True  # type:ignore[arg-type]
        finally:
            ticker.stop()
        assert ticker.ticks >= 10 and roast_flow.current_batch() is None

    def test_the_wait_for_a_cancellation_cannot_be_given_up(self, env:Env) -> None:
        # a cancellation that may or may not have arrived must not be abandoned: Artisan and the ERP would disagree
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        env.choices, env.reasons = [1], ['x']
        with patch.object(roast_flow.background, 'call_with_wait', wraps=background.call_with_wait) as call:
            roast_flow.before_reset(FakeAw())  # type:ignore[arg-type]
        assert call.call_args.kwargs.get('cancellable', False) is False


class TestFinish:
    def test_finishing_writes_weight_out_and_closes_the_batch(self, env:Env) -> None:  # noqa: ARG002
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'finished': FinishedRound('r1', 3, 30.0, 32.0, 25.0, False, 15.0)}]
        aw = FakeAw()
        aw.qmc.weight = (40.0, 0.0, 'Kg')
        aw.qmc.ground_color = 85.0
        assert roast_flow.finish_batch(aw) is True  # type:ignore[arg-type]
        assert aw.qmc.weight == (40.0, 30.0, 'Kg')
        assert roast_flow.current_batch() is None
        assert FakeFinishDialog.created[0].powder_color == '#85'   # Artisan's ground colour becomes the ERP's "Màu bột"
        assert 'Đã kết thúc mẻ' in aw.messages[0] and '30' in aw.messages[0]

    def test_a_weight_outside_the_tolerance_is_told(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        # 26 kg out where 32 are expected: -18.75 % from the formula, tolerance 15 %
        FakeFinishDialog.script = [{'finished': FinishedRound('r1', 3, 26.0, 32.0, 35.0, True, 15.0)}]
        roast_flow.finish_batch(FakeAw(), force=True)  # type:ignore[arg-type]
        text = env.infos[0][0]
        assert 'vượt ngưỡng' in text and '18' in text and '15' in text and '32' in text

    def test_before_drop_it_waits(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        aw = FakeAw()
        aw.qmc.flagstart = True   # recording, DROP not pressed yet
        assert roast_flow.finish_batch(aw) is False  # type:ignore[arg-type]
        assert 'chưa DROP' in env.infos[0][0] and FakeFinishDialog.created == []
        aw.qmc.timeindex[6] = 5000  # DROP pressed
        FakeFinishDialog.script = [{'finished': FinishedRound('r1', 3, 30.0, 32.0, 25.0, False, 15.0)}]
        assert roast_flow.finish_batch(aw) is True  # type:ignore[arg-type]

    def test_after_the_recording_stopped_it_does_not_wait(self, env:Env) -> None:  # noqa: ARG002
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'finished': FinishedRound('r1', 3, 30.0, 32.0, 25.0, False, 15.0)}]
        assert roast_flow.finish_batch(FakeAw()) is True  # type:ignore[arg-type]

    def test_closing_the_dialog_leaves_the_batch_open(self, env:Env) -> None:  # noqa: ARG002
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{}]
        assert roast_flow.finish_batch(FakeAw()) is False  # type:ignore[arg-type]
        assert roast_flow.current_batch() is not None

    def test_a_batch_that_is_over_elsewhere_is_closed_here_too(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'over': True}]
        assert roast_flow.finish_batch(FakeAw()) is True  # type:ignore[arg-type]
        assert roast_flow.current_batch() is None and 'ở nơi khác' in env.infos[0][0]

    def test_expired_login_leaves_the_batch_open(self, env:Env) -> None:  # noqa: ARG002
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'need_login': True}]
        assert roast_flow.finish_batch(FakeAw()) is False  # type:ignore[arg-type]
        assert roast_flow.current_batch() is not None

    def test_nothing_to_finish(self, env:Env) -> None:  # noqa: ARG002
        assert roast_flow.finish_batch(FakeAw()) is True  # type:ignore[arg-type]

    def test_stopping_the_recording_finishes_an_open_batch(self, env:Env) -> None:  # noqa: ARG002
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'finished': FinishedRound('r1', 3, 30.0, 32.0, 25.0, False, 15.0)}]
        with patch.object(roast_flow.QTimer, 'singleShot', lambda _ms, fn: fn()):
            roast_flow.roast_stopped(FakeAw())  # type:ignore[arg-type]
        assert roast_flow.current_batch() is None

    def test_stopping_without_a_batch_does_nothing(self, env:Env) -> None:  # noqa: ARG002
        with patch.object(roast_flow.QTimer, 'singleShot') as shot:
            roast_flow.roast_stopped(FakeAw())  # type:ignore[arg-type]
        shot.assert_not_called()


class TestReset:
    def test_without_a_batch_reset_goes_on(self, env:Env) -> None:  # noqa: ARG002
        assert roast_flow.before_reset(FakeAw()) is True  # type:ignore[arg-type]

    def test_keeping_the_batch_stops_the_reset(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        env.choices = [0]
        assert roast_flow.before_reset(FakeAw()) is False  # type:ignore[arg-type]
        assert roast_flow.current_batch() is not None
        env.api.cancel_round.assert_not_called()

    def test_cancelling_sends_the_reason_and_lets_the_reset_go_on(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        env.choices, env.reasons = [1], ['rang hỏng']
        assert roast_flow.before_reset(FakeAw()) is True  # type:ignore[arg-type]
        env.api.cancel_round.assert_called_once_with('r1', reason='rang hỏng')
        assert roast_flow.current_batch() is None

    def test_no_reason_no_cancellation(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        env.choices, env.reasons = [1], [None]
        assert roast_flow.before_reset(FakeAw()) is False  # type:ignore[arg-type]
        env.api.cancel_round.assert_not_called()
        assert roast_flow.current_batch() is not None

    def test_a_refused_cancellation_keeps_everything(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        env.choices, env.reasons = [1], ['x']
        env.api.cancel_round.side_effect = roasters.RoasterError('không được')
        assert roast_flow.before_reset(FakeAw()) is False  # type:ignore[arg-type]
        assert roast_flow.current_batch() is not None and 'Không huỷ được' in env.infos[0][0]

    def test_a_batch_that_is_over_already_counts_as_cancelled(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        env.choices, env.reasons = [1], ['x']
        env.api.cancel_round.side_effect = rounds.RoundNotRunning('xong rồi')
        assert roast_flow.before_reset(FakeAw()) is True  # type:ignore[arg-type]
        assert roast_flow.current_batch() is None


class TestResume:
    def test_nothing_running_nothing_asked(self, env:Env, qapp:QApplication) -> None:
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        settle(qapp)
        assert env.asked == [] and roast_flow.current_batch() is None
        env.api.active_round.assert_called_once_with('eq1')

    def test_without_a_lease_nothing_is_asked(self, env:Env, qapp:QApplication) -> None:
        roast_flow.resume_if_any(FakeAw(), None)  # type:ignore[arg-type]
        settle(qapp)
        env.api.active_round.assert_not_called()

    def test_an_erp_failure_is_not_fatal(self, env:Env, qapp:QApplication) -> None:  
        env.api.active_round.side_effect = roasters.RoasterUnavailable('network', 'down')
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        settle(qapp)
        assert roast_flow.current_batch() is None

    def test_somebody_elses_batch_is_reported(self, env:Env, qapp:QApplication) -> None:
        env.api.active_round.return_value = active(mine=False)
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        settle(qapp)
        assert 'của người khác' in env.infos[0][0] and roast_flow.current_batch() is None

    def test_my_batch_can_be_finished_right_away(self, env:Env, qapp:QApplication) -> None:
        env.api.active_round.return_value = active()
        env.choices = [0]
        FakeFinishDialog.script = [{'finished': FinishedRound('r9', 2, 9.0, 8.5, 10.0, False, 12.0)}]
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        settle(qapp)
        assert roast_flow.current_batch() is None and FakeFinishDialog.created[0].batch.round_id == 'r9'

    def test_my_batch_can_wait(self, env:Env, qapp:QApplication) -> None:
        env.api.active_round.return_value = active()
        env.choices = [1]
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        settle(qapp)
        b = roast_flow.current_batch()
        assert b is not None and b.round_id == 'r9' and b.equipment_id == 'eq1' and FakeFinishDialog.created == []
        assert 'bắt đầu' in env.asked[0][0] or 'chưa kết thúc' in env.asked[0][0]

    def test_my_batch_can_be_cancelled(self, env:Env, qapp:QApplication) -> None:
        env.api.active_round.return_value = active()
        env.choices, env.reasons = [2], ['Artisan sập, rang lại']
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        settle(qapp)
        env.api.cancel_round.assert_called_once_with('r9', reason='Artisan sập, rang lại')
        assert roast_flow.current_batch() is None

    def test_a_cancellation_without_a_reason_keeps_the_batch(self, env:Env, qapp:QApplication) -> None:
        env.api.active_round.return_value = active()
        env.choices, env.reasons = [2], [None]
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        settle(qapp)
        env.api.cancel_round.assert_not_called()
        assert roast_flow.current_batch() is not None


    def test_it_does_not_wait_for_the_erp(self, env:Env, qapp:QApplication) -> None:
        env.api.active_round.side_effect = slowly(0.4, active(mine=False))
        started_at = time.monotonic()
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        assert time.monotonic() - started_at < 0.2 and env.infos == []  # the main window is usable at once
        settle(qapp)
        assert 'của người khác' in env.infos[0][0]  # and the answer is acted on when it arrives

    def test_a_batch_started_meanwhile_is_left_alone(self, env:Env, qapp:QApplication) -> None:
        env.api.active_round.side_effect = slowly(0.2, active(mine=True))
        roast_flow.resume_if_any(FakeAw(), lease())  # type:ignore[arg-type]
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access  # START was pressed before the ERP answered
        settle(qapp)
        assert env.asked == [] and env.infos == []
        b = roast_flow.current_batch()
        assert b is not None and b.round_id == 'r1'

    def test_a_roaster_given_up_meanwhile_is_not_asked_about(self, env:Env, qapp:QApplication) -> None:
        lse = lease()
        env.api.active_round.side_effect = slowly(0.2, active(mine=False))
        roast_flow.resume_if_any(FakeAw(), lse)  # type:ignore[arg-type]
        lse.is_ok = False  # the lease was lost (or released) while the ERP was thinking
        settle(qapp)
        assert env.infos == [] and env.asked == []


class TestQuit:
    def test_no_batch_quits_freely(self, env:Env) -> None:
        assert roast_flow.confirm_quit(FakeAw()) is True  # type:ignore[arg-type]
        assert env.asked == []

    def test_an_open_batch_is_warned_about(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        env.choices = [0]
        assert roast_flow.confirm_quit(FakeAw()) is False  # type:ignore[arg-type]
        env.choices = [1]
        assert roast_flow.confirm_quit(FakeAw()) is True  # type:ignore[arg-type]
        assert 'LS-1026-000123' in env.asked[0][0]


class TestText:
    def test_title_and_banner(self, env:Env) -> None:  # noqa: ARG002
        assert roast_flow.title_suffix() == '' and roast_flow.banner_text() == ''
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        assert 'LS-1026-000123 · mẻ #3' in roast_flow.title_suffix() and '40' in roast_flow.title_suffix()
        text = roast_flow.banner_text()
        assert 'LS-1026-000123 · mẻ #3' in text and 'Arabica rang' in text and '40' in text


class TestProfileLink:
    def test_the_link_belongs_to_the_recording_of_the_batch_only(self, env:Env) -> None:  # noqa: ARG002
        FakeStartDialog.script = [accepted_start()]
        aw = FakeAw()
        assert start(aw) is True
        assert roast_flow.profile_link(aw) == {}      # nothing recorded yet  # type:ignore[arg-type]
        aw.qmc.roastepoch = 1111
        roast_flow.recording_started(aw)  # type:ignore[arg-type]
        assert roast_flow.profile_link(aw) == {'erpRoundId': 'r1', 'erpCommandCode': 'LS-1026-000123', 'erpRoundNumber': 3}  # type:ignore[arg-type]
        aw.qmc.roastepoch = 2222                       # another profile is shown
        assert roast_flow.profile_link(aw) == {}  # type:ignore[arg-type]

    def test_the_link_survives_finishing_the_batch(self, env:Env) -> None:
        FakeStartDialog.script = [accepted_start()]
        aw = FakeAw()
        start(aw)
        roast_flow.recording_started(aw)  # type:ignore[arg-type]
        FakeFinishDialog.script = [{'finished': FinishedRound('r1', 3, 30.0, 32.0, 25.0, False, 15.0)}]
        roast_flow.finish_batch(aw, force=True)  # type:ignore[arg-type]
        assert roast_flow.profile_link(aw)['erpRoundId'] == 'r1'  # type:ignore[arg-type]
        assert env.infos == []

    def test_reset_forgets_the_link(self, env:Env) -> None:
        FakeStartDialog.script = [accepted_start()]
        aw = FakeAw()
        start(aw)
        roast_flow.recording_started(aw)  # type:ignore[arg-type]
        env.choices, env.reasons = [1], ['x']
        roast_flow.before_reset(aw)  # type:ignore[arg-type]
        assert roast_flow.profile_link(aw) == {}  # type:ignore[arg-type]


class TestFiles:
    """After the ERP took the weight, the files of the batch (.alog, PDF) are built and queued, once the roast stopped recording."""

    @pytest.fixture
    def uploads(self, env:Env) -> Generator[SimpleNamespace, None, None]:  # noqa: ARG002
        queue, uploader = MagicMock(), MagicMock()
        roast_flow.set_uploads(queue, uploader)
        with patch.object(roast_flow.roast_files, 'build_and_enqueue', return_value=[MagicMock()]) as build, \
             patch.object(roast_flow.roast_files, 'wait_cursor', return_value=MagicMock()):
            yield SimpleNamespace(queue=queue, uploader=uploader, build=build)

    @staticmethod
    def recorded(aw:FakeAw) -> None:
        """A batch started and its recording began (that is what ties the profile to it)."""
        FakeStartDialog.script = [accepted_start()]
        assert start(aw) is True
        roast_flow.recording_started(aw)  # type:ignore[arg-type]

    @staticmethod
    def finish(aw:FakeAw) -> None:
        FakeFinishDialog.script = [{'finished': FinishedRound('r1', 3, 30.0, 32.0, 25.0, False, 15.0)}]
        assert roast_flow.finish_batch(aw, force=True)  # type:ignore[arg-type]

    def test_finished_after_the_recording_stopped_builds_the_files_now(self, env:Env, uploads:SimpleNamespace) -> None:  # noqa: ARG002
        aw = FakeAw()
        self.recorded(aw)
        self.finish(aw)
        uploads.build.assert_called_once()
        assert uploads.build.call_args.args[1].round_id == 'r1' and uploads.build.call_args.args[2] is uploads.queue
        uploads.uploader.wake.assert_called_once()

    def test_finished_while_recording_waits_for_the_stop(self, env:Env, uploads:SimpleNamespace) -> None:  # noqa: ARG002
        aw = FakeAw()
        self.recorded(aw)
        aw.qmc.flagstart = True        # still recording (finished with the banner button after DROP)
        self.finish(aw)
        uploads.build.assert_not_called()   # the recording is not complete yet
        aw.qmc.flagstart = False
        with patch.object(roast_flow.QTimer, 'singleShot', lambda _ms, fn: fn()):
            roast_flow.roast_stopped(aw)  # type:ignore[arg-type]
        uploads.build.assert_called_once()
        uploads.uploader.wake.assert_called_once()
        with patch.object(roast_flow.QTimer, 'singleShot') as shot:
            roast_flow.roast_stopped(aw)  # type:ignore[arg-type]
        shot.assert_not_called()      # built once: nothing is waiting any more

    def test_files_are_not_built_for_another_recording(self, env:Env, uploads:SimpleNamespace) -> None:  # noqa: ARG002
        aw = FakeAw()
        self.recorded(aw)
        aw.qmc.flagstart = True
        self.finish(aw)
        aw.qmc.flagstart = False
        aw.qmc.roastepoch = 9999       # RESET and another recording since
        with patch.object(roast_flow.QTimer, 'singleShot', lambda _ms, fn: fn()):
            roast_flow.roast_stopped(aw)  # type:ignore[arg-type]
        uploads.build.assert_not_called()

    def test_reset_forgets_the_files_that_were_waiting(self, env:Env, uploads:SimpleNamespace) -> None:
        aw = FakeAw()
        self.recorded(aw)
        aw.qmc.flagstart = True
        self.finish(aw)
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access  # a new batch is open: RESET asks
        env.choices, env.reasons = [1], ['x']
        assert roast_flow.before_reset(aw) is True  # type:ignore[arg-type]
        assert roast_flow._flow.files_wait is None  # pylint: disable=protected-access
        aw.qmc.flagstart = False
        roast_flow._flow.batch = None  # pylint: disable=protected-access
        with patch.object(roast_flow.QTimer, 'singleShot') as shot:
            roast_flow.roast_stopped(aw)  # type:ignore[arg-type]
        shot.assert_not_called()
        uploads.build.assert_not_called()

    def test_without_a_queue_nothing_is_built(self, env:Env) -> None:  # noqa: ARG002
        roast_flow.set_uploads(None, None)
        aw = FakeAw()
        self.recorded(aw)
        with patch.object(roast_flow.roast_files, 'build_and_enqueue') as build:
            self.finish(aw)
        build.assert_not_called()

    def test_a_batch_that_was_over_elsewhere_sends_no_files(self, env:Env, uploads:SimpleNamespace) -> None:  # noqa: ARG002
        aw = FakeAw()
        self.recorded(aw)
        FakeFinishDialog.script = [{'over': True}]
        assert roast_flow.finish_batch(aw, force=True)  # type:ignore[arg-type]
        uploads.build.assert_not_called()

    def test_a_disk_problem_is_reported_and_does_not_break_finishing(self, env:Env, uploads:SimpleNamespace) -> None:
        uploads.build.side_effect = OSError('disk full')
        aw = FakeAw()
        self.recorded(aw)
        self.finish(aw)
        assert roast_flow.current_batch() is None
        assert any('Không lưu được tệp' in text for text, _detail in env.infos)
        uploads.uploader.wake.assert_not_called()

    def test_no_files_queued_means_no_wake(self, env:Env, uploads:SimpleNamespace) -> None:  # noqa: ARG002
        uploads.build.return_value = []
        aw = FakeAw()
        self.recorded(aw)
        self.finish(aw)
        uploads.uploader.wake.assert_not_called()


def pending_round() -> FinishedRound:
    return FinishedRound('r1', 3, 0.0, 32.0, 0.0, False, 0.0, pending=True)


class TestNotWeighedYet:
    """"Chưa cân được": the batch ends without a weight, the roaster is free at once and the operator can roast the next batch."""

    def test_the_batch_is_closed_without_a_weight_and_without_a_loss_report(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'finished': pending_round()}]
        aw = FakeAw()
        aw.qmc.weight = (40.0, 0.0, 'Kg')
        assert roast_flow.finish_batch(aw) is True  # type:ignore[arg-type]
        assert roast_flow.current_batch() is None and not roast_flow.has_unfinished_batch()
        assert aw.qmc.weight == (40.0, 0.0, 'Kg')   # nothing was weighed: the output stays empty
        assert 'chờ cân' in aw.messages[0] and 'LS-1026-000123' in aw.messages[0]
        assert env.infos == []                      # no modal box: the operator goes on roasting
        env.changed.assert_called()

    def test_start_is_not_blocked_by_a_batch_left_without_a_weight(self, env:Env) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'finished': pending_round()}]
        FakeStartDialog.script = [accepted_start()]
        assert start(FakeAw()) is True
        assert 'Chưa cân được' in env.infos[0][1]   # the reminder tells him he can leave it for later
        b = roast_flow.current_batch()
        assert b is not None and b.round_id == 'r1'

    def test_the_count_of_batches_waiting_goes_up_at_once_and_is_checked_with_the_erp(self, env:Env, qapp:QApplication) -> None:
        roast_flow._flow.batch = a_batch()  # pylint: disable=protected-access
        FakeFinishDialog.script = [{'finished': pending_round()}]
        env.api.list_awaiting_weigh.return_value = [object(), object()]
        roast_flow.finish_batch(FakeAw(), force=True)  # type:ignore[arg-type]
        assert roast_flow.awaiting_count() == 1
        settle(qapp)
        assert roast_flow.awaiting_count() == 2

    def test_the_files_of_the_batch_still_follow(self, env:Env) -> None:  # noqa: ARG002
        queue, uploader = MagicMock(), MagicMock()
        roast_flow.set_uploads(queue, uploader)
        aw = FakeAw()
        TestFiles.recorded(aw)
        FakeFinishDialog.script = [{'finished': pending_round()}]
        with patch.object(roast_flow.roast_files, 'build_and_enqueue', return_value=[MagicMock()]) as build, \
             patch.object(roast_flow.roast_files, 'wait_cursor', return_value=MagicMock()):
            assert roast_flow.finish_batch(aw, force=True)  # type:ignore[arg-type]
        build.assert_called_once()


class TestAwaiting:
    def test_refresh_reads_the_count_from_the_erp(self, env:Env, qapp:QApplication) -> None:
        env.api.list_awaiting_weigh.return_value = [object(), object(), object()]
        assert roast_flow.awaiting_count() is None   # unknown until the ERP answers
        roast_flow.refresh_awaiting()
        settle(qapp)
        assert roast_flow.awaiting_count() == 3
        env.changed.assert_called()

    def test_an_erp_failure_leaves_the_count_unknown(self, env:Env, qapp:QApplication) -> None:
        env.api.list_awaiting_weigh.side_effect = roasters.RoasterUnavailable('network', 'x')
        roast_flow.refresh_awaiting()
        settle(qapp)
        assert roast_flow.awaiting_count() is None

    def test_an_older_erp_without_the_feature_is_not_an_error(self, env:Env, qapp:QApplication) -> None:
        env.api.list_awaiting_weigh.side_effect = roasters.RoasterNotFound('x')
        roast_flow.refresh_awaiting()
        settle(qapp)
        assert roast_flow.awaiting_count() is None and env.infos == []

    def test_opening_the_list_keeps_what_is_left_to_weigh(self, env:Env) -> None:
        FakeWeighDialog.script = [{'remaining': 2}]
        assert roast_flow.open_awaiting(FakeAw(), relogin=lambda: True) is True  # type:ignore[arg-type]
        assert roast_flow.awaiting_count() == 2
        env.changed.assert_called()

    def test_an_expired_login_asks_to_log_in_again(self, env:Env) -> None:  # noqa: ARG002
        FakeWeighDialog.script = [{'need_login': True}]
        relogin = MagicMock(return_value=True)
        assert roast_flow.open_awaiting(FakeAw(), relogin=relogin) is False  # type:ignore[arg-type]
        relogin.assert_called_once()
