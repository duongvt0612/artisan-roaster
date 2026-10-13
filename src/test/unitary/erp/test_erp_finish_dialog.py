"""Unit tests for erp.finish_dialog: ending a roast batch with the weight of the roasted beans (real Qt widgets, offscreen, fake ERP API)."""

import os
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication  # noqa: E402

from erp import finish_dialog, roasters, rounds  # noqa: E402
from erp.batch import Batch  # noqa: E402
from erp.rounds import FinishedRound  # noqa: E402


def batch(**kw:Any) -> Batch:
    base = {'round_id': 'r1', 'round_number': 3, 'command_id': 'c1', 'command_code': 'LS-1026-000123', 'kind': 'normal',
            'output_name': 'Arabica rang', 'output_unit': 'kg', 'input_kg': 40.0, 'expected_output_kg': 32.0, 'tolerance_percent': 15.0,
            'started_at': '2026-10-13T08:00:00+00:00', 'equipment_id': 'eq1'}
    base.update(kw)
    return Batch(**base)


class FakeApi:
    def __init__(self) -> None:
        self.calls:list[dict[str, Any]] = []
        self.error:Exception|None = None

    def finish_round(self, round_id:str, **kw:Any) -> FinishedRound:
        self.calls.append({'round_id': round_id, **kw})
        if self.error is not None:
            raise self.error
        return FinishedRound(round_id, 3, kw['output_kg'], 32.0, 25.0, True, 15.0)


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


def _wait(qapp:QApplication, cond:Any, timeout:float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def make(qapp:QApplication, api:FakeApi, b:Batch|None = None, **kw:Any) -> finish_dialog.FinishRoundDialog:
    d = finish_dialog.FinishRoundDialog(None, MagicMock(), batch=b or batch(), api=api, **kw)
    d.show()
    qapp.processEvents()
    return d


class TestLossText:
    def test_loss_and_limit(self, qapp:QApplication) -> None:  # noqa: ARG002
        text, bad = finish_dialog.loss_text(40, 34, 15)
        assert '15' in text and ('Hao hụt: 15%' in text) and not bad   # exactly at the limit is fine
        text, bad = finish_dialog.loss_text(40, 30, 15)
        assert '25' in text and '15' in text and bad

    def test_no_limit_means_never_red(self, qapp:QApplication) -> None:  # noqa: ARG002
        text, bad = finish_dialog.loss_text(40, 20, 0)
        assert '50' in text and 'tối đa' not in text and not bad

    def test_nothing_typed_yet(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert finish_dialog.loss_text(40, 0, 15) == ('', False)

    def test_more_out_than_in_is_a_doubtful_weighing(self, qapp:QApplication) -> None:  # noqa: ARG002
        text, bad = finish_dialog.loss_text(40, 45, 15)
        assert 'kiểm tra lại cân' in text and bad


class TestDialog:
    def test_shows_what_went_in_and_what_is_expected(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi())
        assert d.windowTitle() == 'Kết thúc mẻ rang'
        labels = ' '.join(label.text() for label in d.findChildren(finish_dialog.QLabel))
        assert 'LS-1026-000123 · mẻ #3' in labels and 'Arabica rang' in labels and '40' in labels and '32' in labels
        assert not d.finish_button.isEnabled()  # the weight has to be typed, never assumed

    def test_a_test_batch_is_labelled(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi(), batch(kind='test'))
        assert any('Mẻ thử' in label.text() for label in d.findChildren(finish_dialog.QLabel))

    def test_loss_follows_the_typed_weight_and_turns_red(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi())
        assert not d.lossLabel.isVisibleTo(d)
        d.spin.setValue(34)
        assert d.lossLabel.isVisibleTo(d) and d.lossLabel.property('bad') is False and d.finish_button.isEnabled()
        d.spin.setValue(30)
        assert d.lossLabel.property('bad') is True
        d.spin.setValue(0)
        assert not d.finish_button.isEnabled()

    def test_success_sends_weight_color_and_notes(self, qapp:QApplication) -> None:
        api = FakeApi()
        d = make(qapp, api, powder_color='#85')
        d.spin.setValue(30)
        d.notesEdit.setText('  ổn  ')
        d.finish_button.click()
        _wait(qapp, lambda: d.result_round is not None)
        assert api.calls == [{'round_id': 'r1', 'output_kg': 30.0, 'notes': 'ổn', 'powder_color': '#85'}]
        assert d.result_round is not None and d.result_round.loss_exceeded and not d.isVisible()

    def test_empty_optional_fields_are_not_sent(self, qapp:QApplication) -> None:
        api = FakeApi()
        d = make(qapp, api)
        d.spin.setValue(30)
        d.finish_button.click()
        _wait(qapp, lambda: d.result_round is not None)
        assert api.calls[0]['notes'] is None and api.calls[0]['powder_color'] is None

    def test_a_network_error_keeps_the_typed_weight(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.error = roasters.RoasterUnavailable('network', 'x')
        d = make(qapp, api)
        d.spin.setValue(30)
        d.finish_button.click()
        _wait(qapp, d.errorLabel.isVisible)
        assert d.isVisible() and 'mạng' in d.errorLabel.text() and d.spin.value() == 30 and d.finish_button.isEnabled()
        assert d.result_round is None

    def test_not_running_any_more_closes_telling_the_caller(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.error = rounds.RoundNotRunning('xong rồi')
        d = make(qapp, api)
        d.spin.setValue(30)
        d.finish_button.click()
        _wait(qapp, lambda: d.already_over)
        assert d.already_over and d.result_round is None and not d.isVisible()

    def test_session_expired_asks_for_login(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.error = roasters.SessionExpired('hết hạn')
        d = make(qapp, api)
        d.spin.setValue(30)
        d.finish_button.click()
        _wait(qapp, lambda: d.need_login)
        assert d.need_login and not d.isVisible()

    def test_forbidden_is_explained(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.error = roasters.RoasterForbidden('Mẻ này do người khác bắt đầu')
        d = make(qapp, api)
        d.spin.setValue(30)
        d.finish_button.click()
        _wait(qapp, d.errorLabel.isVisible)
        assert 'người khác' in d.errorLabel.text() and d.isVisible()

    def test_later_leaves_the_batch_running(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi())
        d.later_button.click()
        assert not d.isVisible() and d.result_round is None and not d.already_over
