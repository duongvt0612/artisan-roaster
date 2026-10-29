"""Unit tests for erp.weigh_dialog: the list of finished batches that still wait for their weight, and typing the weight (real Qt widgets, offscreen, fake ERP API)."""

import os
import time
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from erp import roasters, rounds, weigh_dialog  # noqa: E402
from erp.rounds import AwaitingRound, FinishedRound  # noqa: E402


def awaiting(n:int = 1, **kw:Any) -> AwaitingRound:
    base = {'round_id': f'r{n}', 'round_number': n, 'command_id': 'c1', 'command_code': 'LS-1026-000123', 'kind': 'normal', 'step_number': 1,
            'output_name': 'Arabica rang', 'output_unit': 'kg', 'input_kg': 40.0, 'expected_output_kg': 32.0, 'tolerance_percent': 5.0,
            'completed_at': datetime(2026, 10, 29, 7, 30, tzinfo=UTC), 'roaster_name': 'Máy 15kg'}
    base.update(kw)
    return AwaitingRound(**base)


class FakeApi:
    def __init__(self, *items:AwaitingRound) -> None:
        self.items:list[AwaitingRound] = list(items)
        self.list_error:Exception|None = None
        self.weigh_error:Exception|None = None
        self.weighed:list[dict[str, Any]] = []
        self.list_calls = 0

    def list_awaiting_weigh(self) -> list[AwaitingRound]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return list(self.items)

    def weigh_round(self, round_id:str, **kw:Any) -> FinishedRound:
        self.weighed.append({'round_id': round_id, **kw})
        if self.weigh_error is not None:
            raise self.weigh_error
        self.items = [i for i in self.items if i.round_id != round_id]
        return FinishedRound(round_id, 3, kw['output_kg'], 32.0, 25.0, kw['output_kg'] < 30.4, 5.0)


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


def _wait(qapp:QApplication, cond:Any, timeout:float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def make(qapp:QApplication, api:FakeApi) -> weigh_dialog.WeighPendingDialog:
    d = weigh_dialog.WeighPendingDialog(None, MagicMock(), api=api)
    d.show()
    _wait(qapp, lambda: api.list_calls > 0 and d.statusLabel.text() != '')
    return d


def ids(d:weigh_dialog.WeighPendingDialog) -> list[str]:
    out = []
    for i in range(d.results.count()):
        data = d.results.item(i).data(Qt.ItemDataRole.UserRole)
        out.append(data.round_id)
    return out


class TestTexts:
    def test_title_and_detail_say_what_the_row_is(self, qapp:QApplication) -> None:  # noqa: ARG002
        r = awaiting(3)
        assert weigh_dialog.row_title(r) == 'LS-1026-000123 · mẻ #3 · Arabica rang'
        detail = weigh_dialog.row_detail(r)
        assert 'Nhân vào 40 kg' in detail and 'dự kiến 32 kg' in detail and 'Máy 15kg' in detail and '29/10' in detail

    def test_a_test_batch_is_labelled(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert 'Mẻ thử' in weigh_dialog.row_badge(awaiting(kind='test'))[1]
        assert weigh_dialog.row_badge(awaiting())[1] == 'Chờ cân'

    def test_the_time_is_left_out_when_the_erp_does_not_know_it(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert 'xong lúc' not in weigh_dialog.row_detail(awaiting(completed_at=None))


class TestList:
    def test_lists_the_waiting_batches_and_picks_the_first(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi(awaiting(1), awaiting(2)))
        assert d.windowTitle() == 'Mẻ chờ cân' and ids(d) == ['r1', 'r2']
        sel = d.round_selected()
        assert sel is not None and sel.round_id == 'r1' and d.remaining == 2
        assert d.detail.isVisibleTo(d) and not d.confirm_button.isEnabled()  # the weight has to be typed, never assumed

    def test_nothing_waiting_says_so(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi())
        assert d.results.count() == 0 and 'Không có mẻ nào chờ cân' in d.statusLabel.text()
        assert not d.detail.isVisibleTo(d) and d.remaining == 0

    def test_a_failing_erp_shows_an_error_and_keeps_the_count_unknown(self, qapp:QApplication) -> None:
        api = FakeApi(awaiting())
        api.list_error = roasters.RoasterUnavailable('network', 'x')
        d = weigh_dialog.WeighPendingDialog(None, MagicMock(), api=api)
        d.show()
        _wait(qapp, d.errorLabel.isVisible)
        assert 'mạng' in d.errorLabel.text() and d.remaining is None

    def test_an_erp_without_the_feature_is_explained(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.list_error = roasters.RoasterNotFound('x')
        d = weigh_dialog.WeighPendingDialog(None, MagicMock(), api=api)
        d.show()
        _wait(qapp, d.errorLabel.isVisible)
        assert 'chưa hỗ trợ' in d.errorLabel.text()

    def test_refresh_keeps_the_selection_and_the_typed_weight(self, qapp:QApplication) -> None:
        api = FakeApi(awaiting(1), awaiting(2))
        d = make(qapp, api)
        d.results.setCurrentRow(1)
        d.spin.setValue(29.5)
        api.items.append(awaiting(3))
        calls = api.list_calls
        d.refresh_button.click()
        _wait(qapp, lambda: api.list_calls > calls and d.results.count() == 3)
        sel = d.round_selected()
        assert sel is not None and sel.round_id == 'r2' and d.spin.value() == 29.5 and d.remaining == 3

    def test_expired_login_asks_for_login(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.list_error = roasters.SessionExpired('hết hạn')
        d = weigh_dialog.WeighPendingDialog(None, MagicMock(), api=api)
        d.show()
        _wait(qapp, lambda: d.need_login)
        assert d.need_login and not d.isVisible()


class TestWeighing:
    def test_loss_follows_the_typed_weight_and_turns_red(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi(awaiting()))
        assert not d.lossLabel.isVisibleTo(d)
        d.spin.setValue(31)   # expected 32 kg, tolerance 5 %: -3.1 %
        assert d.lossLabel.isVisibleTo(d) and d.lossLabel.property('bad') is False and d.confirm_button.isEnabled()
        d.spin.setValue(28)   # -12.5 %
        assert d.lossLabel.property('bad') is True
        d.spin.setValue(0)
        assert not d.confirm_button.isEnabled()

    def test_confirming_sends_the_weight_and_the_notes_then_moves_on(self, qapp:QApplication) -> None:
        api = FakeApi(awaiting(1), awaiting(2))
        d = make(qapp, api)
        d.spin.setValue(30)
        d.notesEdit.setText('  cân sau 10 phút ')
        d.confirm_button.click()
        _wait(qapp, lambda: len(d.weighed) == 1)
        assert api.weighed == [{'round_id': 'r1', 'output_kg': 30.0, 'notes': 'cân sau 10 phút'}]
        # the dialog stays open for the next one: the roaster weighs several batches in a row
        assert d.isVisible() and ids(d) == ['r2'] and d.remaining == 1
        sel = d.round_selected()
        assert sel is not None and sel.round_id == 'r2' and d.spin.value() == 0 and d.notesEdit.text() == ''
        assert '30' in d.resultLabel.text() and 'mẻ #1' in d.resultLabel.text() and d.resultLabel.isVisibleTo(d)

    def test_a_result_outside_the_tolerance_is_flagged(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi(awaiting()))
        d.spin.setValue(28)
        d.confirm_button.click()
        _wait(qapp, lambda: len(d.weighed) == 1)
        assert d.resultLabel.property('bad') is True and 'vượt ngưỡng' in d.resultLabel.text()

    def test_the_last_one_leaves_an_empty_list(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi(awaiting()))
        d.spin.setValue(31)
        d.confirm_button.click()
        _wait(qapp, lambda: d.remaining == 0)
        assert d.results.count() == 0 and not d.detail.isVisibleTo(d) and 'Không có mẻ nào chờ cân' in d.statusLabel.text()

    def test_buttons_are_locked_while_sending(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi(awaiting()))
        d.spin.setValue(31)
        d.busy = True
        d._update_buttons()  # pylint: disable=protected-access
        assert not d.confirm_button.isEnabled()

    def test_a_network_error_keeps_the_typed_weight(self, qapp:QApplication) -> None:
        api = FakeApi(awaiting())
        api.weigh_error = roasters.RoasterUnavailable('network', 'x')
        d = make(qapp, api)
        d.spin.setValue(31)
        d.confirm_button.click()
        _wait(qapp, d.errorLabel.isVisible)
        assert 'mạng' in d.errorLabel.text() and d.spin.value() == 31 and d.confirm_button.isEnabled() and d.results.count() == 1

    def test_weighed_elsewhere_drops_the_row_and_says_so(self, qapp:QApplication) -> None:
        api = FakeApi(awaiting(1), awaiting(2))
        api.weigh_error = rounds.RoundNotAwaiting('đã cân')
        d = make(qapp, api)
        d.spin.setValue(31)
        calls = api.list_calls
        d.confirm_button.click()
        _wait(qapp, lambda: api.list_calls > calls)  # the list is read again: the row that was weighed elsewhere is gone
        api.weigh_error = None
        assert 'đã được cân' in d.errorLabel.text() or 'đã được cân' in d.statusLabel.text()

    def test_forbidden_is_explained(self, qapp:QApplication) -> None:
        api = FakeApi(awaiting())
        api.weigh_error = roasters.RoasterForbidden('Mẻ này do người khác bắt đầu')
        d = make(qapp, api)
        d.spin.setValue(31)
        d.confirm_button.click()
        _wait(qapp, d.errorLabel.isVisible)
        assert 'người khác' in d.errorLabel.text() and d.isVisible()

    def test_session_expired_asks_for_login(self, qapp:QApplication) -> None:
        api = FakeApi(awaiting())
        api.weigh_error = roasters.SessionExpired('hết hạn')
        d = make(qapp, api)
        d.spin.setValue(31)
        d.confirm_button.click()
        _wait(qapp, lambda: d.need_login)
        assert d.need_login and not d.isVisible()

    def test_closing_keeps_what_is_left(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi(awaiting(1), awaiting(2)))
        d.close_button.click()
        assert not d.isVisible() and d.remaining == 2
