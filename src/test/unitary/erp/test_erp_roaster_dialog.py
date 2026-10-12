"""Unit tests for erp.roaster_dialog: the mandatory roaster selection (real Qt widgets, offscreen, fake ERP API)."""

import os
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

from erp import roasters, roaster_dialog  # noqa: E402
from erp.roasters import Claim, Holder, Roaster  # noqa: E402


class MemStore:
    def __init__(self) -> None:
        self.data:dict[str, Any] = {}

    def value(self, key:str, default:Any = None) -> Any:
        return self.data.get(key, default)

    def setValue(self, key:str, value:Any) -> None:  # noqa: N802
        self.data[key] = value


def roaster(rid:str, code:str, free:bool = True, mine:bool = False, **kw:Any) -> Roaster:
    held = None if free else Holder('u2', 'Nguyễn B', 'PC-9', datetime(2026, 10, 12, 7, 5, tzinfo=UTC))
    return Roaster(id=rid, code=code, name=kw.get('name', f'Máy {code}'), capacity=kw.get('capacity', '15.00'), capacity_unit='kg',
                   status='free' if free else 'occupied', held_by=held, version=1, mine=mine, held_by_session=kw.get('held_by_session', False))


class FakeApi:
    def __init__(self, machines:list[Roaster]|None = None) -> None:
        self.machines = machines if machines is not None else []
        self.list_calls = 0
        self.list_error:Exception|None = None
        self.claim_error:Exception|None = None
        self.claim_calls:list[dict[str, Any]] = []
        self.force_calls:list[tuple[str, str]] = []
        self.force_error:Exception|None = None

    def list_roasters(self, session_id:str) -> list[Roaster]:
        self.list_calls += 1
        self.session_id_seen = session_id
        if self.list_error is not None:
            raise self.list_error
        return list(self.machines)

    def claim(self, roaster_id:str, **kw:Any) -> Claim:
        self.claim_calls.append({'id': roaster_id, **kw})
        if self.claim_error is not None:
            raise self.claim_error
        return Claim(roaster_id, 'claimed', 7, None, 30, 10)

    def force_release(self, roaster_id:str, *, reason:str) -> None:
        self.force_calls.append((roaster_id, reason))
        if self.force_error is not None:
            raise self.force_error


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


def _wait(qapp:QApplication, cond:Any, timeout:float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def make(qapp:QApplication, api:FakeApi, **kw:Any) -> roaster_dialog.RoasterDialog:
    store = kw.pop('store', MemStore())
    d = roaster_dialog.RoasterDialog(None, MagicMock(), user_name='Administrator', can_force_release=kw.pop('can_force_release', False),
                                     api=api, session_id='s1', device_id='d1', device_name='PC-TEST', store=store,
                                     auto_refresh=False, **kw)
    d.show()
    _wait(qapp, lambda: d.list.count() > 0 or d.statusLabel.text() or d.errorLabel.isVisible())
    return d


def select(d:roaster_dialog.RoasterDialog, rid:str) -> None:
    for i in range(d.list.count()):
        item = d.list.item(i)
        data = item.data(0x0100)  # Qt.ItemDataRole.UserRole
        if data.id == rid:
            d.list.setCurrentItem(item)
            return
    raise AssertionError(f'no row for {rid}')


class TestHelpers:
    def test_badge_kind(self) -> None:
        assert roaster_dialog.badge_kind(roaster('1', 'A')) == 'free'
        assert roaster_dialog.badge_kind(roaster('1', 'A', free=False)) == 'busy'
        assert roaster_dialog.badge_kind(roaster('1', 'A', free=False, mine=True)) == 'mine'

    def test_badge_text_is_words_not_only_colour(self, qapp:QApplication) -> None:  # noqa: ARG002
        texts = {roaster_dialog.badge_text(k) for k in ('free', 'busy', 'mine')}
        assert len(texts) == 3 and all(any(c.isalpha() for c in t) for t in texts)

    def test_format_since_today_and_earlier(self) -> None:
        now = datetime(2026, 10, 12, 20, 0).astimezone()
        today = now.replace(hour=14, minute=5)
        assert roaster_dialog.format_since(today, now) == '14:05'
        assert roaster_dialog.format_since(today - timedelta(days=2), now).startswith('10/10 ')
        assert roaster_dialog.format_since(None) == ''

    def test_holder_text(self, qapp:QApplication) -> None:  # noqa: ARG002
        text = roaster_dialog.holder_text(roaster('1', 'A', free=False))
        assert 'Nguyễn B' in text and 'PC-9' in text and text.count('·') == 2
        assert roaster_dialog.holder_text(roaster('1', 'A')) == ''

    def test_sub_text(self) -> None:
        assert roaster_dialog.sub_text(roaster('1', 'ROAST-01')) == 'ROAST-01 · 15.00 kg'
        assert roaster_dialog.sub_text(roaster('1', 'ROAST-01', capacity=None)) == 'ROAST-01'

    def test_preselect_prefers_the_last_roaster_if_free(self) -> None:
        ms = [roaster('1', 'A'), roaster('2', 'B'), roaster('3', 'C', free=False)]
        assert roaster_dialog.preselect_id(ms, '2') == '2'
        assert roaster_dialog.preselect_id(ms, '3') == '1'   # the last one is busy now: first free
        assert roaster_dialog.preselect_id(ms, None) == '1'
        assert roaster_dialog.preselect_id([roaster('3', 'C', free=False)], '3') is None


class TestList:
    def test_shows_every_roaster_and_preselects_the_last_free_one(self, qapp:QApplication) -> None:
        store = MemStore()
        store.data['lastRoasterId'] = 'b'
        d = make(qapp, FakeApi([roaster('a', 'ROAST-01'), roaster('b', 'ROAST-02'), roaster('c', 'ROAST-03', free=False)]), store=store)
        assert d.list.count() == 3
        assert d._current_id() == 'b'  # pylint: disable=protected-access
        assert d.ok_button.isEnabled()
        d.reject()

    def test_session_id_is_sent_to_mark_the_holder_session(self, qapp:QApplication) -> None:
        api = FakeApi([roaster('a', 'ROAST-01')])
        d = make(qapp, api)
        assert api.session_id_seen == 's1'
        d.reject()

    def test_occupied_roaster_cannot_be_chosen(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([roaster('a', 'ROAST-01', free=False)]))
        select(d, 'a')
        assert not d.ok_button.isEnabled()
        d.reject()

    def test_own_roaster_in_another_session_can_be_chosen(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([roaster('a', 'ROAST-01', free=False, mine=True)]))
        select(d, 'a')
        assert d.ok_button.isEnabled()
        d.reject()

    def test_empty_list_says_so(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([]))
        assert d.list.count() == 0
        assert 'máy rang' in d.statusLabel.text()
        assert not d.ok_button.isEnabled()
        d.reject()

    def test_refresh_keeps_the_selection(self, qapp:QApplication) -> None:
        api = FakeApi([roaster('a', 'ROAST-01'), roaster('b', 'ROAST-02')])
        d = make(qapp, api)
        select(d, 'b')
        calls = api.list_calls
        api.machines = [roaster('a', 'ROAST-01', free=False), roaster('b', 'ROAST-02')]
        d.refresh()
        _wait(qapp, lambda: api.list_calls > calls)
        _wait(qapp, lambda: d.list.count() == 2)
        assert d._current_id() == 'b'  # pylint: disable=protected-access
        d.reject()

    def test_realtime_request_reloads_the_list(self, qapp:QApplication) -> None:
        api = FakeApi([roaster('a', 'ROAST-01')])
        d = make(qapp, api)
        calls = api.list_calls
        d.request_refresh()
        _wait(qapp, lambda: api.list_calls > calls)
        assert api.list_calls > calls
        d.reject()


class TestErrors:
    def test_forbidden_explains_the_missing_permission(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.list_error = roasters.RoasterForbidden('Bạn không có quyền')
        d = make(qapp, api)
        assert d.errorLabel.isVisible() and 'roasters:operate' in d.errorLabel.text()
        d.reject()

    def test_network_error_tells_to_retry(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.list_error = roasters.RoasterUnavailable('network', 'down')
        d = make(qapp, api)
        assert d.errorLabel.isVisible() and 'kết nối' in d.errorLabel.text()
        d.reject()

    def test_expired_login_ends_the_dialog_and_asks_for_a_new_login(self, qapp:QApplication) -> None:
        api = FakeApi()
        api.list_error = roasters.SessionExpired('hết hạn')
        d = roaster_dialog.RoasterDialog(None, MagicMock(), user_name='x', can_force_release=False, api=api, session_id='s',
                                         device_id='d', device_name='PC', store=MemStore(), auto_refresh=False)
        d.show()
        _wait(qapp, lambda: d.need_login)
        assert d.need_login and d.result() == QDialog.DialogCode.Rejected


class TestChoose:
    def test_claims_with_this_pc_identity_and_accepts(self, qapp:QApplication) -> None:
        store = MemStore()
        api = FakeApi([roaster('a', 'ROAST-01'), roaster('b', 'ROAST-02')])
        d = make(qapp, api, store=store)
        select(d, 'b')
        d._choose()  # pylint: disable=protected-access
        _wait(qapp, lambda: d.result() == QDialog.DialogCode.Accepted)
        assert d.result() == QDialog.DialogCode.Accepted
        assert api.claim_calls == [{'id': 'b', 'session_id': 's1', 'device_id': 'd1', 'device_name': 'PC-TEST'}]
        assert d.roaster is not None and d.roaster.id == 'b'
        assert d.claim is not None and d.claim.version == 7
        assert store.data['lastRoasterId'] == 'b'  # remembered for next time

    def test_occupied_in_the_meantime_shows_who_and_stays_open(self, qapp:QApplication) -> None:
        api = FakeApi([roaster('a', 'ROAST-01')])
        d = make(qapp, api)
        api.claim_error = roasters.RoasterOccupied('Máy rang đang được Nguyễn B sử dụng trên PC-9')
        api.machines = [roaster('a', 'ROAST-01', free=False)]
        d._choose()  # pylint: disable=protected-access
        _wait(qapp, d.errorLabel.isVisible)
        assert 'Nguyễn B' in d.errorLabel.text()
        assert d.result() != QDialog.DialogCode.Accepted
        assert d.claim is None and d.roaster is None
        _wait(qapp, lambda: d._current() is not None and roaster_dialog.badge_kind(d._current()) == 'busy')  # pylint: disable=protected-access
        assert not d.ok_button.isEnabled()  # the refreshed list shows it as taken
        d.reject()

    def test_buttons_are_locked_while_claiming(self, qapp:QApplication) -> None:
        api = FakeApi([roaster('a', 'ROAST-01')])
        d = make(qapp, api)
        d._set_busy(True)  # pylint: disable=protected-access
        assert not d.ok_button.isEnabled() and not d.refresh_button.isEnabled() and not d.list.isEnabled()
        d._set_busy(False)  # pylint: disable=protected-access
        assert d.ok_button.isEnabled()
        d.reject()

    def test_closing_without_a_roaster_leaves_nothing_chosen(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([roaster('a', 'ROAST-01')]))
        d.reject()
        assert d.result() == QDialog.DialogCode.Rejected and d.claim is None


class TestForceRelease:
    def test_button_is_hidden_without_the_permission(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([roaster('a', 'ROAST-01', free=False)]), can_force_release=False)
        assert d.force_button.isHidden()
        d.reject()

    def test_enabled_only_for_a_roaster_somebody_else_holds(self, qapp:QApplication) -> None:
        d = make(qapp, FakeApi([roaster('a', 'ROAST-01'), roaster('b', 'ROAST-02', free=False),
                                roaster('c', 'ROAST-03', free=False, mine=True, held_by_session=True)]), can_force_release=True)
        assert not d.force_button.isHidden()
        select(d, 'a')
        assert not d.force_button.isEnabled()   # free
        select(d, 'b')
        assert d.force_button.isEnabled()
        select(d, 'c')
        assert not d.force_button.isEnabled()   # this very session: nothing to force
        d.reject()

    def test_confirmed_force_release_calls_the_erp_with_the_reason_and_reloads(self, qapp:QApplication) -> None:
        api = FakeApi([roaster('b', 'ROAST-02', free=False)])
        asked:list[str] = []
        d = make(qapp, api, can_force_release=True, confirm_force=lambda r: (asked.append(r.id) or True, 'PC bị treo'))
        select(d, 'b')
        calls = api.list_calls
        d._force()  # pylint: disable=protected-access
        _wait(qapp, lambda: api.force_calls and api.list_calls > calls)
        assert asked == ['b'] and api.force_calls == [('b', 'PC bị treo')]
        assert api.list_calls > calls  # the list is reloaded to show the roaster free
        d.reject()

    def test_declined_confirmation_does_nothing(self, qapp:QApplication) -> None:
        api = FakeApi([roaster('b', 'ROAST-02', free=False)])
        d = make(qapp, api, can_force_release=True, confirm_force=lambda _r: (False, ''))
        select(d, 'b')
        d._force()  # pylint: disable=protected-access
        qapp.processEvents()
        assert api.force_calls == []
        d.reject()

    def test_not_held_error_is_shown(self, qapp:QApplication) -> None:
        api = FakeApi([roaster('b', 'ROAST-02', free=False)])
        api.force_error = roasters.NotHeld('Máy rang đang trống')
        d = make(qapp, api, can_force_release=True, confirm_force=lambda _r: (True, ''))
        select(d, 'b')
        d._force()  # pylint: disable=protected-access
        _wait(qapp, d.errorLabel.isVisible)
        assert 'trống' in d.errorLabel.text()
        d.reject()
