"""Unit tests for erp.start_dialog: choosing the production command when START is pressed (real Qt widgets, offscreen, fake ERP API)."""

import os
import time
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication  # noqa: E402

from erp import roasters, rounds, start_dialog  # noqa: E402
from erp.rounds import RoastCommand, RoundInput, SaleOrder, StartedRound  # noqa: E402


def cmd(cid:str, code:str, *, name:str = 'Arabica rang', target:float = 120, remaining:float = 80, kind:str = 'normal',
        status:str = 'pending', inputs:tuple[RoundInput, ...]|None = None, orders:tuple[SaleOrder, ...] = (), request:str = 'YC-1') -> RoastCommand:
    ins = inputs if inputs is not None else (RoundInput(f'g-{cid}', 'NX-A', 'Nhân A', 'kg', 55.5),)
    return RoastCommand(command_id=cid, code=code, kind=kind, status=status, step_number=1, output_name=name, output_code='TP', output_unit='kg',
                        target_kg=target, produced_kg=target - remaining, remaining_kg=remaining, request_code=request, sale_orders=orders, inputs=ins)


class FakeApi:
    def __init__(self, commands:list[RoastCommand]|None = None) -> None:
        self.commands = commands if commands is not None else []
        self.queries:list[str] = []
        self.search_error:Exception|None = None
        self.start_error:Exception|None = None
        self.start_calls:list[dict[str, Any]] = []

    def search_commands(self, query:str, limit:int = 20) -> list[RoastCommand]:
        self.queries.append(query)
        if self.search_error is not None:
            raise self.search_error
        q = query.strip().lower()
        return [c for c in self.commands if q in c.code.lower() or q in c.output_name.lower()][:limit]

    def start_round(self, **kw:Any) -> StartedRound:
        self.start_calls.append(kw)
        if self.start_error is not None:
            raise self.start_error
        return StartedRound('r1', 3, kw['command_id'], kw['step_number'], 32.0, 15.0, datetime(2026, 10, 13, 8, 0, tzinfo=UTC))


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


def _wait(qapp:QApplication, cond:Any, timeout:float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def make(qapp:QApplication, api:FakeApi, **kw:Any) -> start_dialog.StartRoundDialog:
    d = start_dialog.StartRoundDialog(None, MagicMock(), roaster_name='Máy Rang Nhỏ (ROAST-01)', equipment_id='eq1', version=4,
                                      api=api, session_id='s1', debounce_ms=0, **kw)
    d.show()
    _wait(qapp, lambda: d.results.count() > 0 or 'Không có' in d.statusLabel.text() or d.errorLabel.isVisible())
    return d


def pick(qapp:QApplication, d:start_dialog.StartRoundDialog, cid:str) -> None:
    for i in range(d.results.count()):
        item = d.results.item(i)
        if item.data(0x0100).command_id == cid:  # Qt.ItemDataRole.UserRole
            d.results.setCurrentItem(item)
            qapp.processEvents()
            return
    raise AssertionError(f'no row for {cid}')


class TestHelpers:
    def test_title_says_what_is_produced_and_how_many_kg(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert start_dialog.command_title(cmd('1', 'LS-1026-000123', name='Arabica Cầu Đất rang', target=120)) == 'LS-1026-000123 · Arabica Cầu Đất rang · 120 kg'
        assert start_dialog.command_title(cmd('1', 'LS-1', target=0.125)).endswith('· 0,125 kg') or start_dialog.command_title(cmd('1', 'LS-1', target=0.125)).endswith('· 0.125 kg')

    def test_format_kg_trims_zeros(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert start_dialog.format_kg(120.0) == '120'
        assert start_dialog.format_kg(80.5) in ('80,5', '80.5')
        assert start_dialog.format_kg(0) == '0'

    def test_orders_text(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert start_dialog.orders_text(cmd('1', 'LS', orders=(SaleOrder('SO-7', 'Khách A'),))) == 'SO-7 (Khách A)'
        many = tuple(SaleOrder(f'SO-{i}', f'K{i}') for i in range(4))
        assert start_dialog.orders_text(cmd('1', 'LS', orders=many)) == 'SO-0 (K0), SO-1 (K1) +2'
        assert start_dialog.orders_text(cmd('1', 'LS', orders=(SaleOrder('SO-7', ''),))) == 'SO-7'
        assert start_dialog.orders_text(cmd('1', 'LS', request='YC-5')) == 'YC-5'

    def test_detail_line(self, qapp:QApplication) -> None:  # noqa: ARG002
        text = start_dialog.command_detail(cmd('1', 'LS', remaining=80, orders=(SaleOrder('SO-7', 'Khách A'),)))
        assert text == 'Còn lại 80 kg · SO-7 (Khách A)'

    def test_badges_are_words(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert start_dialog.command_badge(cmd('1', 'LS', kind='test'))[0] == 'test'
        assert start_dialog.command_badge(cmd('1', 'LS', status='in_progress')) == ('progress', 'Đang thực hiện')
        assert start_dialog.command_badge(cmd('1', 'LS', status='pending')) == ('pending', 'Chờ thực hiện')
        assert start_dialog.command_badge(cmd('1', 'LS', kind='test', status='in_progress'))[1] == 'Mẻ thử'


class TestSearch:
    def test_lists_commands_on_open_without_typing(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1', name='Arabica'), cmd('2', 'LS-2', name='Robusta')])
        d = make(qapp, api)
        assert d.results.count() == 2 and api.queries[0] == ''
        assert not d.start_button.isEnabled()
        assert not d.detail.isVisible()

    def test_typing_filters_the_list(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1', name='Arabica'), cmd('2', 'LS-2', name='Robusta')])
        d = make(qapp, api)
        d.search_edit.setText('robu')
        _wait(qapp, lambda: d.results.count() == 1)
        assert d.results.count() == 1 and d.results.item(0).data(0x0100).code == 'LS-2'
        assert api.queries[-1] == 'robu'

    def test_no_match(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]))
        d.search_edit.setText('zzz')
        _wait(qapp, lambda: d.results.count() == 0)
        assert 'Không có lệnh' in d.statusLabel.text()

    def test_a_stale_answer_does_not_overwrite_a_newer_one(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]))
        d._seq = 10  # pylint: disable=protected-access
        d._on_done('search', 3, [cmd('9', 'LS-OLD')], None)  # pylint: disable=protected-access
        assert [d.results.item(i).data(0x0100).code for i in range(d.results.count())] == ['LS-1']

    def test_search_error_is_shown(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.search_error = roasters.RoasterUnavailable('network', 'x')
        d = make(qapp, api)
        assert d.errorLabel.isVisible() and 'mạng' in d.errorLabel.text()

    def test_session_expired_closes_asking_for_login(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.search_error = roasters.SessionExpired('hết hạn')
        d = make(qapp, api)
        assert d.need_login and not d.isVisible()


class TestPick:
    def test_picking_shows_the_output_and_the_beans_to_declare(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1', name='Arabica Cầu Đất rang', target=120)]))
        pick(qapp, d, '1')
        assert d.detail.isVisible() and 'Arabica Cầu Đất rang' in d.detailTitle.text() and '120' in d.detailTitle.text()
        assert len(d._spins) == 1 and d._spins[0][1].maximum() == 55.5  # pylint: disable=protected-access
        assert not d.start_button.isEnabled()  # he has to type the kg he loaded

    def test_start_needs_some_kg(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]))
        pick(qapp, d, '1')
        d._spins[0][1].setValue(12.5)  # pylint: disable=protected-access
        assert d.start_button.isEnabled() and '12' in d.totalLabel.text()
        d._spins[0][1].setValue(0)  # pylint: disable=protected-access
        assert not d.start_button.isEnabled()

    def test_cannot_type_more_than_what_is_available(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]))
        pick(qapp, d, '1')
        d._spins[0][1].setValue(999)  # pylint: disable=protected-access
        assert d._spins[0][1].value() == 55.5  # pylint: disable=protected-access

    def test_a_command_without_beans_cannot_be_started(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1', inputs=())]))
        pick(qapp, d, '1')
        assert d._spins == [] and not d.start_button.isEnabled()  # pylint: disable=protected-access

    def test_warns_above_the_roaster_capacity(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]), capacity_kg=10.0)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(12)  # pylint: disable=protected-access
        assert d.warnLabel.isVisibleTo(d) and 'công suất' in d.warnLabel.text()
        d._spins[0][1].setValue(8)  # pylint: disable=protected-access
        assert not d.warnLabel.isVisibleTo(d)

    def test_the_kg_typed_survive_a_new_search(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1', name='Arabica'), cmd('2', 'LS-2', name='Arabica 2')])
        d = make(qapp, api)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(20)  # pylint: disable=protected-access
        before = len(api.queries)
        d.search_edit.setText('arabica')
        _wait(qapp, lambda: len(api.queries) > before)
        _wait(qapp, lambda: False, 0.3)  # let the answer arrive
        selected = d.command_selected()
        assert selected is not None and selected.command_id == '1'
        assert d._spins[0][1].value() == 20  # pylint: disable=protected-access


class TestStart:
    def _ready(self, qapp:QApplication, api:FakeApi) -> start_dialog.StartRoundDialog:
        d = make(qapp, api)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(40)  # pylint: disable=protected-access
        return d

    def test_success_sends_the_lease_and_closes(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        d = self._ready(qapp, api)
        d.start_button.click()
        _wait(qapp, lambda: d.started is not None)
        assert d.started is not None and d.started.round_id == 'r1'
        assert d.command is not None and d.command.code == 'LS-1'
        assert [(i.inventory_id, kg) for i, kg in d.input_lines] == [('g-1', 40.0)]
        call = api.start_calls[0]
        assert (call['equipment_id'], call['session_id'], call['version'], call['command_id'], call['step_number']) == ('eq1', 's1', 4, '1', 1)
        assert call['inputs'] == [('g-1', 40.0)]
        assert not d.isVisible()

    @pytest.mark.parametrize(('error', 'flag'), [
        (roasters.LeaseLost('mất máy'), 'lease_lost'),
        (rounds.RoasterBusy('bận'), 'busy_with_round'),
        (roasters.SessionExpired('hết hạn'), 'need_login'),
    ])
    def test_refusals_keep_it_from_starting(self, qapp:QApplication, error:Exception, flag:str) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        d = self._ready(qapp, api)
        api.start_error = error
        d.start_button.click()
        _wait(qapp, lambda: getattr(d, flag))
        assert getattr(d, flag) and d.started is None

    def test_a_refusal_is_explained_and_the_dialog_stays_open(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        d = self._ready(qapp, api)
        api.start_error = roasters.RoasterError('Nguyên liệu vượt quá số lượng còn lại')
        d.start_button.click()
        _wait(qapp, d.errorLabel.isVisible)
        assert d.isVisible() and 'vượt quá' in d.errorLabel.text() and d.started is None
        assert d.start_button.isEnabled()  # he can correct and try again

    def test_quota_error_refreshes_what_is_left(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        d = self._ready(qapp, api)
        before = len(api.queries)
        api.start_error = rounds.QuotaExceeded('vượt')
        d.start_button.click()
        _wait(qapp, lambda: len(api.queries) > before)
        assert len(api.queries) > before and d.started is None

    def test_cancel_rejects(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]))
        d.cancel_button.click()
        assert not d.isVisible() and d.started is None
