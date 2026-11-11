"""Unit tests for erp.start_dialog: choosing the production command when START is pressed (real Qt widgets, offscreen, fake ERP API)."""

import os
import time
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

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
        self.include_empty:list[bool] = []

    def search_commands(self, query:str, limit:int = 20, include_empty:bool = False) -> list[RoastCommand]:
        self.queries.append(query)
        self.include_empty.append(include_empty)
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


class TestFineInputs:
    """Nguyên liệu mã wholePackOnly (màng lọc...) tiêu hao tới 0,0001 kg: ô nhập 4 chữ số; nguyên liệu khác giữ 3 chữ số như cũ."""

    @staticmethod
    def _fine_command() -> RoastCommand:
        return cmd('1', 'LS-1', inputs=(RoundInput('film', 'FILM', 'Màng lọc', 'kg', 12.4996, True), RoundInput('g-1', 'NX-A', 'Nhân A', 'kg', 55.5)))

    def test_format_kg_shows_up_to_four_decimals(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert start_dialog.format_kg(0.0004) == '0.0004'
        assert start_dialog.format_kg(12.4996) == '12.4996'
        assert start_dialog.format_kg(7.35) == '7.35'
        assert start_dialog.format_kg(1234.5) == '1,234.5'
        assert start_dialog.format_kg(0.00004) == '0'

    def test_fine_input_has_four_decimal_spin_and_others_keep_three(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([self._fine_command()]))
        pick(qapp, d, '1')
        fine, plain = d._spins[0][1], d._spins[1][1]  # pylint: disable=protected-access
        assert (fine.decimals(), plain.decimals()) == (4, 3)
        # Ô số theo quy ước ERP (05/11/2026): locale cố định en-US, hiện dấu "," ngăn nghìn, không có menu dán.
        assert fine.locale().name() == 'en_US' and fine.isGroupSeparatorShown()
        assert plain.contextMenuPolicy() == start_dialog.Qt.ContextMenuPolicy.NoContextMenu
        assert fine.singleStep() == 0.0001
        fine.setValue(0.0004)
        assert fine.value() == 0.0004 and d.start_button.isEnabled()
        assert d.total_kg() == 0.0004
        assert '0.0004' in d.totalLabel.text()

    def test_the_fine_kg_are_sent_to_the_erp_untouched(self, qapp:QApplication) -> None:
        api = FakeApi([self._fine_command()])
        d = make(qapp, api)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(0.0004)  # pylint: disable=protected-access
        d._spins[1][1].setValue(40)  # pylint: disable=protected-access
        d.start_button.click()
        _wait(qapp, lambda: d.started is not None)
        assert api.start_calls[0]['inputs'] == [('film', 0.0004), ('g-1', 40.0)]


class TestHelpers:
    def test_title_says_what_is_produced_and_how_many_kg(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert start_dialog.command_title(cmd('1', 'LS-1026-000123', name='Arabica Cầu Đất rang', target=120)) == 'LS-1026-000123 · Arabica Cầu Đất rang · 120 kg'
        assert start_dialog.command_title(cmd('1', 'LS-1', target=0.125)).endswith('· 0.125 kg')
        assert start_dialog.command_title(cmd('1', 'LS-1', target=1500)).endswith('· 1,500 kg')

    def test_format_kg_trims_zeros(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert start_dialog.format_kg(120.0) == '120'
        assert start_dialog.format_kg(80.5) == '80.5'
        assert start_dialog.format_kg(0) == '0'

    def test_orders_text(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert start_dialog.orders_text(cmd('1', 'LS', orders=(SaleOrder('SO-7', 'Khách A'),))) == 'SO-7 (Khách A)'
        many = tuple(SaleOrder(f'SO-{i}', f'K{i}') for i in range(4))
        assert start_dialog.orders_text(cmd('1', 'LS', orders=many)) == 'SO-0 (K0), SO-1 (K1) +2'
        assert start_dialog.orders_text(cmd('1', 'LS', orders=(SaleOrder('SO-7', ''),))) == 'SO-7'
        assert start_dialog.orders_text(cmd('1', 'LS', request='YC-5')) == 'YC-5'

    def test_detail_line(self, qapp:QApplication) -> None:  # noqa: ARG002
        c = replace(cmd('1', 'LS', target=3.18, remaining=1.98, orders=(SaleOrder('SO-7', 'Khách A'),)),
                    input_exported_kg=3.75, input_used_kg=1.2, input_remaining_kg=2.55)
        assert start_dialog.command_detail(c) == ('Đầu ra: mục tiêu 3.18 · đã ra 1.2 · còn 1.98 kg\n'
                                                  'Nhân: đã xuất 3.75 · đã dùng 1.2 · còn 2.55 kg\n'
                                                  'SO-7 (Khách A)')
        assert '\n' not in start_dialog.accessible_text(c)

    def test_detail_line_without_exported_beans(self, qapp:QApplication) -> None:  # noqa: ARG002
        text = start_dialog.command_detail(cmd('1', 'LS', target=5, remaining=5, request=''))
        assert text == 'Đầu ra: mục tiêu 5 · đã ra 0 · còn 5 kg\nNhân: chưa xuất kho'

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


class TestRefresh:
    def test_the_button_loads_the_commands_created_meanwhile(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        d = make(qapp, api)
        assert d.results.count() == 1
        api.commands.append(cmd('2', 'LS-2'))
        d.refresh_button.click()
        _wait(qapp, lambda: d.results.count() == 2)
        assert d.results.count() == 2 and '2 lệnh' in d.statusLabel.text()

    def test_it_keeps_the_text_searched(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1', name='Arabica'), cmd('2', 'LS-2', name='Robusta')])
        d = make(qapp, api)
        d.search_edit.setText('robu')
        _wait(qapp, lambda: d.results.count() == 1)
        api.commands.append(cmd('3', 'LS-3', name='Robusta 2'))
        d.refresh_button.click()
        _wait(qapp, lambda: d.results.count() == 2)
        assert api.queries[-1] == 'robu'

    def test_it_keeps_the_pick_and_the_kg_typed(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        d = make(qapp, api)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(20)  # pylint: disable=protected-access
        api.commands.append(cmd('2', 'LS-2'))
        d.refresh_button.click()
        _wait(qapp, lambda: d.results.count() == 2)
        selected = d.command_selected()
        assert selected is not None and selected.command_id == '1'
        assert d._spins[0][1].value() == 20  # pylint: disable=protected-access

    def test_a_pick_that_is_gone_is_dropped(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1'), cmd('2', 'LS-2')])
        d = make(qapp, api)
        pick(qapp, d, '1')
        api.commands = [cmd('2', 'LS-2')]
        d.refresh_button.click()
        _wait(qapp, lambda: d.results.count() == 1)
        assert d.command_selected() is None and not d.start_button.isEnabled()

    def test_a_failure_is_shown(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        d = make(qapp, api)
        api.search_error = roasters.RoasterUnavailable('network', 'x')
        d.refresh_button.click()
        _wait(qapp, lambda: d.errorLabel.isVisible())
        assert 'mạng' in d.errorLabel.text()

    def test_it_is_off_while_the_batch_is_being_started(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]))
        d._set_busy(True)  # pylint: disable=protected-access
        assert not d.refresh_button.isEnabled()
        d._set_busy(False)  # pylint: disable=protected-access
        assert d.refresh_button.isEnabled()


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
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]), capacity_kg=10.0, max_kg=12.0)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(11)  # pylint: disable=protected-access
        assert d.warnLabel.isVisibleTo(d) and 'dung sai' in d.warnLabel.text() and d.start_button.isEnabled()
        d._spins[0][1].setValue(8)  # pylint: disable=protected-access
        assert not d.warnLabel.isVisibleTo(d)

    # Phản hồi xưởng 11/11/2026: máy 1.2 kg nhồi được tới 1.3 kg (dung sai 0.1): vượt 1.3 thì không cho bắt đầu.
    def test_blocks_above_capacity_plus_tolerance(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]), capacity_kg=1.2, max_kg=1.3)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(1.3)  # pylint: disable=protected-access
        assert d.start_button.isEnabled() and not d.over_max()
        d._spins[0][1].setValue(1.301)  # pylint: disable=protected-access
        assert d.over_max() and not d.start_button.isEnabled()
        assert d.warnLabel.isVisibleTo(d) and '1.3 kg' in d.warnLabel.text() and d.warnLabel.property('level') == 'block'

    def test_without_tolerance_the_capacity_is_the_maximum(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]), capacity_kg=1.2)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(1.25)  # pylint: disable=protected-access
        assert d.over_max() and not d.start_button.isEnabled()

    def test_an_unknown_capacity_never_blocks(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([cmd('1', 'LS-1')]))
        pick(qapp, d, '1')
        d._spins[0][1].setValue(50)  # pylint: disable=protected-access
        assert not d.over_max() and d.start_button.isEnabled() and not d.warnLabel.isVisibleTo(d)

    def test_capacity_refused_by_the_erp_is_shown(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        api.start_error = rounds.CapacityExceeded('Tổng 1.4 kg nhân vượt mức tối đa 1.3 kg của Máy 1kg')
        d = make(qapp, api)
        pick(qapp, d, '1')
        d._spins[0][1].setValue(1.4)  # pylint: disable=protected-access
        d._start()  # pylint: disable=protected-access
        _wait(qapp, lambda: d.errorLabel.isVisible())
        assert 'vượt mức tối đa' in d.errorLabel.text() and d.started is None

    def test_the_input_hint_shows_exported_used_and_left(self, qapp:QApplication) -> None:
        bean = RoundInput('g-1', 'NX-A', 'Nhân A', 'kg', 2.55, exported_kg=3.75, used_kg=1.2)
        d = make(qapp, FakeApi([cmd('1', 'LS-1', inputs=(bean,))]))
        pick(qapp, d, '1')
        texts = [w.text() for w in d.inputsBox.findChildren(QLabel)]
        assert 'Đã xuất 3.75 · đã dùng 1.2 · còn 2.55 kg' in texts

    def test_show_empty_commands_switch_searches_again(self, qapp:QApplication) -> None:
        api = FakeApi([cmd('1', 'LS-1')])
        d = make(qapp, api)
        assert api.include_empty[-1] is False and not d.show_empty.isChecked()
        d.show_empty.setChecked(True)
        _wait(qapp, lambda: len(api.include_empty) >= 2)
        assert api.include_empty[-1] is True

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
