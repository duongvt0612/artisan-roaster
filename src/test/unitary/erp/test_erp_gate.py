"""Unit tests for erp.gate: the mandatory ERP login before the main window is shown."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from erp import client, config, gate


@pytest.fixture
def aw() -> MagicMock:
    return MagicMock()


@pytest.fixture
def session() -> MagicMock:
    with patch.object(gate, 'session') as s:
        s.is_authenticated = False
        s.stored_username.return_value = 'admin'
        s.ended_message = None
        yield s


def _network_error() -> client.ErpAuthError:
    return client.ErpAuthError('network', 'down')


class TestRequireLogin:
    def test_restored_session_goes_straight_in(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.return_value = True
        with patch.object(gate, 'erp_login') as login, patch.object(gate, 'ask_retry') as retry:
            assert gate.require_login(aw) is True
        login.assert_not_called()
        retry.assert_not_called()

    def test_nothing_to_restore_shows_login_and_accepts_success(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.return_value = False
        with patch.object(gate, 'erp_login', return_value=True) as login:
            assert gate.require_login(aw) is True
        login.assert_called_once_with(aw, 'admin')

    def test_session_ended_by_the_erp_is_explained_before_the_login(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.return_value = False
        session.ended_message = 'Phiên đăng nhập đã bị quản trị viên kết thúc.'
        order:list[str] = []
        with patch.object(gate, 'show_session_ended', side_effect=lambda m: order.append(m)) as shown, \
             patch.object(gate, 'erp_login', side_effect=lambda *_a: order.append('login') or True):
            assert gate.require_login(aw) is True
        shown.assert_called_once_with('Phiên đăng nhập đã bị quản trị viên kết thúc.')
        assert order == ['Phiên đăng nhập đã bị quản trị viên kết thúc.', 'login']

    def test_plain_expiry_goes_to_the_login_without_a_message(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.return_value = False
        with patch.object(gate, 'show_session_ended') as shown, patch.object(gate, 'erp_login', return_value=True):
            gate.require_login(aw)
        shown.assert_not_called()

    def test_cancelled_login_refuses_entry(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.return_value = False
        with patch.object(gate, 'erp_login', return_value=False):
            assert gate.require_login(aw) is False

    def test_network_error_while_restoring_asks_to_retry_and_never_lets_in_offline(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.side_effect = _network_error()
        with patch.object(gate, 'ask_retry', return_value=False) as retry, patch.object(gate, 'erp_login') as login:
            assert gate.require_login(aw) is False
        retry.assert_called_once()
        login.assert_not_called()

    def test_retry_after_network_error_can_succeed(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.side_effect = [_network_error(), True]
        with patch.object(gate, 'ask_retry', return_value=True) as retry:
            assert gate.require_login(aw) is True
        retry.assert_called_once()
        assert session.restore.call_count == 2

    def test_retry_falls_back_to_login_if_the_session_turned_out_invalid(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.side_effect = [_network_error(), False]
        with patch.object(gate, 'ask_retry', return_value=True), patch.object(gate, 'erp_login', return_value=True) as login:
            assert gate.require_login(aw) is True
        login.assert_called_once()

    @pytest.mark.parametrize('kind', ['server', 'rate_limited'])
    def test_other_transient_errors_also_ask_to_retry(self, aw:MagicMock, session:MagicMock, kind:client.ErrorKind) -> None:
        session.restore.side_effect = client.ErpAuthError(kind, 'x')
        with patch.object(gate, 'ask_retry', return_value=False) as retry:
            assert gate.require_login(aw) is False
        retry.assert_called_once()


class TestRealtimeSessionRevoked:
    def test_forgets_the_session_and_tells_the_lease(self, session:MagicMock) -> None:
        manager = MagicMock()
        gate._reset_state()
        gate._state.manager = manager
        try:
            gate._on_realtime_session_revoked('kicked_admin')
        finally:
            gate._reset_state()
        session.revoke.assert_called_once_with('kicked_admin')
        manager.notify_session_revoked.assert_called_once_with('kicked_admin')

    def test_works_before_a_roaster_was_chosen(self, session:MagicMock) -> None:
        gate._reset_state()
        gate._on_realtime_session_revoked('')
        session.revoke.assert_called_once_with('')


class TestLogoutAction:
    def test_logout_clears_the_session_and_asks_the_app_to_quit(self, aw:MagicMock, session:MagicMock) -> None:
        with patch.object(gate, 'confirm_logout', return_value=True):
            assert gate.logout(aw) is True
        session.logout.assert_called_once()

    def test_declined_confirmation_keeps_the_session(self, aw:MagicMock, session:MagicMock) -> None:
        with patch.object(gate, 'confirm_logout', return_value=False):
            assert gate.logout(aw) is False
        session.logout.assert_not_called()


# ---- the roaster this session holds ------------------------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from erp.lease import LeaseState  # noqa: E402


class FakeDialog:
    """Stands in for RoasterDialog: exec() 'chooses' according to the script."""

    script:list[dict[str, object]] = []
    created:list['FakeDialog'] = []

    def __init__(self, _parent:object, _aw:object, *, user_name:str, can_force_release:bool) -> None:
        self.user_name, self.can_force_release = user_name, can_force_release
        step = FakeDialog.script.pop(0)
        self.accept = bool(step.get('accept', False))
        self.claim = step.get('claim')
        self.roaster = step.get('roaster')
        self.need_login = bool(step.get('need_login', False))
        self.refreshed = 0
        FakeDialog.created.append(self)

    def exec(self) -> int:
        return 1 if self.accept else 0

    def deleteLater(self) -> None:  # noqa: N802
        pass

    def request_refresh(self) -> None:
        self.refreshed += 1


def _roaster(rid:str = 'eq1', name:str = 'Máy Rang Nhỏ', capacity:str|None = '15.00', unit:str|None = 'kg') -> SimpleNamespace:
    return SimpleNamespace(id=rid, name=name, code='ROAST-01', capacity=capacity, capacity_unit=unit)


def _claim() -> SimpleNamespace:
    return SimpleNamespace(version=3, ttl_seconds=30, heartbeat_seconds=10)


@pytest.fixture
def roaster_env(session:MagicMock) -> object:  # noqa: ARG001
    FakeDialog.script, FakeDialog.created = [], []
    gate._reset_state()  # pylint: disable=protected-access
    session.user = {'fullName': 'Administrator', 'roles': [{'permissions': [{'code': 'roasters:operate'}]}]}
    with patch.object(gate, 'RoasterDialog', FakeDialog), \
         patch.object(gate, 'LeaseManager') as manager_cls, \
         patch.object(gate.erp_realtime, 'is_available', return_value=False), \
         patch.object(gate.roast_flow, 'resume_if_any') as resume, \
         patch.object(gate, '_start_uploads') as start_uploads, \
         patch.object(gate.erp_banner, 'install') as install:
        install.return_value = MagicMock()
        yield SimpleNamespace(manager_cls=manager_cls, install=install, resume=resume, start_uploads=start_uploads)
    gate._reset_state()  # pylint: disable=protected-access


class TestPermissions:
    def test_codes_are_collected_from_every_role(self) -> None:
        user = {'roles': [{'permissions': [{'code': 'a:b'}, {'code': 'c:d'}]}, {'permissions': [{'code': 'a:b'}]}, {'permissions': None}]}
        assert gate.permission_codes(user) == {'a:b', 'c:d'}

    @pytest.mark.parametrize('user', [None, {}, {'roles': None}, {'roles': [None]}, {'roles': [{'permissions': [None, {'code': 5}]}]}])
    def test_malformed_users_give_no_permissions(self, user:object) -> None:
        assert gate.permission_codes(user) == set()  # type:ignore[arg-type]


class TestRequireRoaster:
    def test_choosing_a_roaster_lets_the_user_in_and_keeps_it_alive(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        assert gate.require_roaster(aw) is True
        roaster_env.manager_cls.assert_called_once()
        manager = roaster_env.manager_cls.return_value
        manager.start.assert_called_once()
        assert gate.current_lease() is manager
        assert FakeDialog.created[0].user_name == 'Administrator'
        roaster_env.resume.assert_called_once_with(aw, manager)  # a batch left running by a crash is offered for finishing
        roaster_env.start_uploads.assert_called_once_with()      # and the files of earlier batches are sent from now on

    def test_closing_the_dialog_keeps_the_user_out(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': False}]
        assert gate.require_roaster(aw) is False
        roaster_env.manager_cls.assert_not_called()
        assert gate.current_lease() is None

    def test_force_release_is_offered_only_to_users_with_the_permission(self, aw:MagicMock, roaster_env:SimpleNamespace, session:MagicMock) -> None:  # noqa: ARG002
        FakeDialog.script = [{'accept': False}]
        gate.require_roaster(aw)
        assert FakeDialog.created[-1].can_force_release is False
        session.user = {'roles': [{'permissions': [{'code': 'roasters:force-release'}]}]}
        FakeDialog.script = [{'accept': False}]
        gate.require_roaster(aw)
        assert FakeDialog.created[-1].can_force_release is True

    def test_expired_login_asks_to_log_in_again_then_to_choose_again(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'need_login': True}, {'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        with patch.object(gate, 'require_login', return_value=True) as login:
            assert gate.require_roaster(aw) is True
        login.assert_called_once_with(aw)
        assert len(FakeDialog.created) == 2
        roaster_env.manager_cls.assert_called_once()

    def test_expired_login_that_is_not_renewed_keeps_the_user_out(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'need_login': True}]
        with patch.object(gate, 'require_login', return_value=False):
            assert gate.require_roaster(aw) is False
        roaster_env.manager_cls.assert_not_called()

    def test_banner_installation_failure_never_blocks_entry(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        roaster_env.install.side_effect = RuntimeError('layout differs')
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        assert gate.require_roaster(aw) is True

    def test_realtime_is_started_when_available_and_stopped_when_refused(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:  # noqa: ARG002
        with patch.object(gate.erp_realtime, 'is_available', return_value=True), \
             patch.object(gate.erp_realtime, 'RealtimeClient') as client_cls:
            FakeDialog.script = [{'accept': False}]
            assert gate.require_roaster(aw) is False
        client_cls.return_value.start.assert_called_once()
        client_cls.return_value.stop.assert_called_once()

    def test_realtime_changed_event_refreshes_the_open_dialog(self, roaster_env:SimpleNamespace) -> None:  # noqa: ARG002
        d = FakeDialog.__new__(FakeDialog)
        d.refreshed = 0
        gate._state.dialog = d  # type:ignore[assignment] # pylint: disable=protected-access
        gate._on_realtime_changed({'equipmentId': 'eq1'})  # pylint: disable=protected-access
        assert d.refreshed == 1

    def test_realtime_lease_lost_event_reaches_the_manager(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        gate._on_realtime_lease_lost({'equipmentId': 'eq1', 'reason': 'force_released'})  # pylint: disable=protected-access
        roaster_env.manager_cls.return_value.notify_lease_lost.assert_called_once_with('eq1', 'force_released')

    def test_choosing_again_gives_back_the_previous_roaster(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster('eq1')},
                             {'accept': True, 'claim': _claim(), 'roaster': _roaster('eq2')}]
        gate.require_roaster(aw)
        first = roaster_env.manager_cls.return_value
        first.is_ok = True
        assert gate.reselect_roaster() is True
        first.stop.assert_called_once_with(release=True)

    def test_choosing_again_after_losing_the_roaster_does_not_release_it(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster('eq1')},
                             {'accept': True, 'claim': _claim(), 'roaster': _roaster('eq2')}]
        gate.require_roaster(aw)
        first = roaster_env.manager_cls.return_value
        first.is_ok = False   # LOST: nothing to give back
        gate.reselect_roaster()
        first.stop.assert_called_once_with(release=False)

    def test_cancelling_the_reselection_keeps_the_banner_and_the_old_state(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster('eq1')}, {'accept': False}]
        gate.require_roaster(aw)
        assert gate.reselect_roaster() is False
        assert gate.current_lease() is roaster_env.manager_cls.return_value


class TestLeaseBanner:
    def test_state_changes_drive_the_banner(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        bar = roaster_env.install.return_value
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        roaster_env.manager_cls.return_value.roaster_name = 'Máy Rang Nhỏ'
        gate.require_roaster(aw)
        bar.apply.assert_called_with(LeaseState.HELD, 'Máy Rang Nhỏ', '')
        gate._on_lease_state(LeaseState.LOST, 'force_released')  # pylint: disable=protected-access
        bar.apply.assert_called_with(LeaseState.LOST, 'Máy Rang Nhỏ', 'force_released')


class TestReleaseRoaster:
    def test_gives_the_roaster_back_once(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        manager = roaster_env.manager_cls.return_value
        gate.release_roaster()
        gate.release_roaster()
        manager.stop.assert_called_once()
        assert gate.current_lease() is None

    def test_is_safe_without_a_roaster(self, roaster_env:SimpleNamespace) -> None:  # noqa: ARG002
        gate.release_roaster()

    def test_never_waits_longer_than_the_timeout_for_a_slow_erp(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        import time
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        roaster_env.manager_cls.return_value.stop.side_effect = lambda **_k: time.sleep(2)
        started = time.monotonic()
        gate.release_roaster(timeout=0.1)
        assert time.monotonic() - started < 1.0   # quitting Artisan must not hang on a dead network

    def test_logout_gives_the_roaster_back_before_ending_the_erp_session(self, aw:MagicMock, roaster_env:SimpleNamespace, session:MagicMock) -> None:
        order:list[str] = []
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        roaster_env.manager_cls.return_value.stop.side_effect = lambda **_k: order.append('release')
        session.logout.side_effect = lambda: order.append('logout')
        with patch.object(gate, 'confirm_logout', return_value=True):
            assert gate.logout(aw) is True
        assert order == ['release', 'logout']


class TestWindowTitle:
    """The roaster this session holds is always visible in the window title, even if the banner is missed."""

    def test_no_roaster_no_suffix(self, roaster_env:SimpleNamespace) -> None:  # noqa: ARG002
        assert gate.window_title_suffix() == ''

    def test_held_names_the_roaster(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        manager = roaster_env.manager_cls.return_value
        manager.state = LeaseState.HELD
        manager.roaster_name = 'Máy Rang Nhỏ (ROAST-01)'
        assert gate.window_title_suffix() == ' · Máy rang: Máy Rang Nhỏ (ROAST-01)'

    def test_suspect_and_lost_are_called_out(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        manager = roaster_env.manager_cls.return_value
        manager.roaster_name = 'Máy Rang Nhỏ (ROAST-01)'
        manager.state = LeaseState.SUSPECT
        assert 'mất kết nối' in gate.window_title_suffix()
        manager.state = LeaseState.LOST
        assert 'KHÔNG CÒN GIỮ' in gate.window_title_suffix()

    def test_the_title_is_refreshed_when_a_roaster_is_adopted_and_when_the_state_changes(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:  # noqa: ARG002
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        aw.updateWindowTitle.assert_called()
        aw.updateWindowTitle.reset_mock()
        gate._on_lease_state(LeaseState.LOST, 'lease_lost')  # pylint: disable=protected-access
        aw.updateWindowTitle.assert_called_once()

    def test_a_failing_title_refresh_never_breaks_the_flow(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:  # noqa: ARG002
        aw.updateWindowTitle.side_effect = RuntimeError('boom')
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        assert gate.require_roaster(aw) is True

    def test_the_name_includes_the_code_when_both_exist(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        assert roaster_env.manager_cls.call_args.args[1] == 'Máy Rang Nhỏ (ROAST-01)'


class TestRoastBatch:
    """The gate carries the roast batch (erp.roast_flow) into the banner, the window title, logout and quit."""

    @pytest.fixture(autouse=True)
    def _fresh_flow(self) -> Generator[None, None, None]:
        gate._reset_state()  # pylint: disable=protected-access
        gate.roast_flow._reset_state()  # pylint: disable=protected-access
        yield
        gate._reset_state()  # pylint: disable=protected-access
        gate.roast_flow._reset_state()  # pylint: disable=protected-access

    @staticmethod
    def _a_batch() -> object:
        from erp.batch import Batch
        return Batch('r1', 3, 'c1', 'LS-1026-000123', 'normal', 'Arabica rang', 'kg', 40.0, 32.0, 15.0, '', 'eq1')

    @pytest.mark.parametrize(('capacity', 'unit', 'expected'), [
        ('15.00', 'kg', 15.0), ('15', None, 15.0), ('15', 'KG', 15.0), ('15', 'lb', None), (None, 'kg', None), ('abc', 'kg', None), ('0', 'kg', None), ('-3', 'kg', None),
    ])
    def test_capacity_of_the_chosen_roaster(self, capacity:str|None, unit:str|None, expected:float|None) -> None:
        assert gate._capacity_kg(_roaster(capacity=capacity, unit=unit)) == expected  # pylint: disable=protected-access

    def test_adopting_a_roaster_remembers_its_capacity(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:  # noqa: ARG002
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster(capacity='12.5')}]
        gate.require_roaster(aw)
        assert gate._state.capacity_kg == 12.5  # pylint: disable=protected-access

    def test_start_is_handed_the_roaster_and_its_capacity(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster(capacity='12.5')}]
        gate.require_roaster(aw)
        with patch.object(gate.roast_flow, 'before_start', return_value=True) as flow:
            assert gate.before_start(aw) is True
        args, kwargs = flow.call_args
        assert args == (aw, roaster_env.manager_cls.return_value) and kwargs['capacity_kg'] == 12.5
        with patch.object(gate, 'require_login', return_value=True) as login:
            kwargs['relogin']()
        login.assert_called_once_with(aw)

    def test_the_banner_finish_button_finishes_the_batch(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        bar = roaster_env.install.return_value
        (slot,), _ = bar.finish_batch.connect.call_args
        with patch.object(gate.roast_flow, 'finish_batch') as finish:
            slot()
        finish.assert_called_once_with(aw)

    def test_a_batch_change_updates_the_banner_and_the_title(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        bar = roaster_env.install.return_value
        aw.updateWindowTitle.reset_mock()
        gate.roast_flow._flow.batch = self._a_batch()  # pylint: disable=protected-access
        gate.roast_flow._changed()  # pylint: disable=protected-access
        text = bar.set_batch.call_args.args[0]
        assert 'LS-1026-000123 · mẻ #3' in text and 'Arabica rang' in text
        aw.updateWindowTitle.assert_called()

    def test_a_batch_change_also_refreshes_the_number_of_files_to_send(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        queue = MagicMock()
        queue.pending_count.return_value = 2
        gate._state.queue = queue  # pylint: disable=protected-access
        bar = roaster_env.install.return_value
        gate._on_batch_changed()  # pylint: disable=protected-access
        bar.set_pending_files.assert_called_with(2)

    def test_the_title_names_the_batch(self, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        manager = roaster_env.manager_cls.return_value
        manager.state = LeaseState.HELD
        manager.roaster_name = 'Máy Rang Nhỏ (ROAST-01)'
        gate.roast_flow._flow.batch = self._a_batch()  # pylint: disable=protected-access
        title = gate.window_title_suffix()
        assert title.startswith(' · Máy rang: Máy Rang Nhỏ (ROAST-01)') and 'Đang rang LS-1026-000123 · mẻ #3' in title

    def test_logout_with_an_open_batch_asks_first_and_can_be_cancelled(self, aw:MagicMock, session:MagicMock) -> None:
        gate.roast_flow._flow.batch = self._a_batch()  # pylint: disable=protected-access
        with patch.object(gate, 'confirm_logout', return_value=True), patch.object(gate.roast_flow, '_choose', return_value=0) as ask:
            assert gate.logout(aw) is False
        ask.assert_called_once()
        session.logout.assert_not_called()

    def test_logout_confirmed_with_an_open_batch_does_not_ask_again_when_quitting(self, aw:MagicMock, session:MagicMock) -> None:
        gate.roast_flow._flow.batch = self._a_batch()  # pylint: disable=protected-access
        with patch.object(gate, 'confirm_logout', return_value=True), patch.object(gate.roast_flow, '_choose', return_value=1) as ask:
            assert gate.logout(aw) is True
            assert gate.confirm_quit(aw) is True   # the quit that follows the logout
        assert ask.call_count == 1
        session.logout.assert_called_once()

    def test_quitting_with_an_open_batch_asks(self, aw:MagicMock) -> None:
        gate.roast_flow._flow.batch = self._a_batch()  # pylint: disable=protected-access
        with patch.object(gate.roast_flow, '_choose', return_value=0):
            assert gate.confirm_quit(aw) is False
        with patch.object(gate.roast_flow, '_choose', return_value=1):
            assert gate.confirm_quit(aw) is True

    def test_quitting_without_a_batch_never_asks(self, aw:MagicMock) -> None:
        with patch.object(gate.roast_flow, '_choose') as ask:
            assert gate.confirm_quit(aw) is True
        ask.assert_not_called()

    def test_the_wrappers_reach_the_flow(self, aw:MagicMock) -> None:
        with patch.object(gate.roast_flow, 'recording_started') as started, patch.object(gate.roast_flow, 'roast_stopped') as stopped, \
             patch.object(gate.roast_flow, 'before_reset', return_value=False) as reset, patch.object(gate.roast_flow, 'profile_link', return_value={'a': 1}) as link:
            gate.recording_started(aw)
            gate.roast_stopped(aw)
            assert gate.before_reset(aw) is False
            assert gate.profile_link(aw) == {'a': 1}
        for m in (started, stopped, reset, link):
            m.assert_called_once_with(aw)


class TestUploads:
    """The sender of the files of finished batches: started with the roaster, stopped with it, resumed by a login, reported on the banner."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> Generator[None, None, None]:
        gate._reset_state()  # pylint: disable=protected-access
        gate.roast_flow._reset_state()  # pylint: disable=protected-access
        yield
        gate._stop_uploads()  # pylint: disable=protected-access
        gate._reset_state()  # pylint: disable=protected-access
        gate.roast_flow._reset_state()  # pylint: disable=protected-access

    def test_start_creates_the_queue_and_the_sender_once(self, aw:MagicMock) -> None:  # noqa: ARG002
        with patch.object(gate, 'UploadQueue') as queue_cls, patch.object(gate, 'Uploader') as uploader_cls:
            queue_cls.return_value.pending_count.return_value = 2
            gate._start_uploads()  # pylint: disable=protected-access
            gate._start_uploads()  # pylint: disable=protected-access
        queue_cls.assert_called_once()
        uploader_cls.assert_called_once_with(queue_cls.return_value)
        uploader_cls.return_value.start.assert_called_once()
        assert gate.roast_flow._flow.queue is queue_cls.return_value  # pylint: disable=protected-access
        assert gate.roast_flow._flow.uploader is uploader_cls.return_value  # pylint: disable=protected-access

    def test_a_failing_start_never_breaks_anything(self, aw:MagicMock) -> None:  # noqa: ARG002
        with patch.object(gate, 'UploadQueue', side_effect=OSError('read-only disk')):
            gate._start_uploads()  # pylint: disable=protected-access
        assert gate._state.uploader is None  # pylint: disable=protected-access

    def test_stop_does_not_wait_and_switches_the_flow_off(self, aw:MagicMock) -> None:  # noqa: ARG002
        with patch.object(gate, 'UploadQueue'), patch.object(gate, 'Uploader') as uploader_cls:
            gate._start_uploads()  # pylint: disable=protected-access
            gate._stop_uploads()  # pylint: disable=protected-access
        uploader_cls.return_value.stop.assert_called_once_with(wait=False)
        assert gate.roast_flow._flow.queue is None and gate._state.uploader is None  # pylint: disable=protected-access

    def test_release_roaster_stops_the_sender_too(self, aw:MagicMock) -> None:  # noqa: ARG002
        with patch.object(gate, 'UploadQueue'), patch.object(gate, 'Uploader') as uploader_cls:
            gate._start_uploads()  # pylint: disable=protected-access
            gate.release_roaster()
        uploader_cls.return_value.stop.assert_called_once_with(wait=False)

    def test_a_login_sends_what_waited_for_it(self, aw:MagicMock, session:MagicMock) -> None:
        with patch.object(gate, 'UploadQueue'), patch.object(gate, 'Uploader') as uploader_cls:
            gate._start_uploads()  # pylint: disable=protected-access
            session.restore.return_value = True
            assert gate.require_login(aw) is True
            uploader_cls.return_value.resume.assert_called_once()
            uploader_cls.return_value.resume.reset_mock()
            session.restore.return_value = False
            with patch.object(gate, 'erp_login', return_value=True):
                assert gate.require_login(aw) is True
            uploader_cls.return_value.resume.assert_called_once()
            uploader_cls.return_value.resume.reset_mock()
            with patch.object(gate, 'erp_login', return_value=False):
                assert gate.require_login(aw) is False
            uploader_cls.return_value.resume.assert_not_called()

    def test_login_without_a_sender_is_fine(self, aw:MagicMock, session:MagicMock) -> None:
        session.restore.return_value = True
        assert gate.require_login(aw) is True

    def test_events_update_the_banner_and_the_status_line(self, aw:MagicMock) -> None:
        from erp.attachments import Job
        banner = MagicMock()
        gate._state.banner = banner  # pylint: disable=protected-access
        gate._state.aw = aw  # pylint: disable=protected-access
        queue = MagicMock()
        queue.pending_count.return_value = 3
        gate._state.queue = queue  # pylint: disable=protected-access
        job = Job('j1', 'r1', 'pdf', 'LS-1-m3.pdf', 'ab' * 32, 10, 1.0)
        gate._on_upload_event('sent', job, '')  # pylint: disable=protected-access
        banner.set_pending_files.assert_called_with(3)
        assert 'LS-1-m3.pdf' in aw.sendmessage.call_args.args[0]

    def test_a_refused_file_is_explained_with_where_it_was_kept(self, aw:MagicMock) -> None:
        from erp.attachments import Job
        gate._state.aw = aw  # pylint: disable=protected-access
        queue = MagicMock()
        queue.pending_count.return_value = 0
        queue.rejected_directory = '/data/erp_upload_queue/rejected'
        gate._state.queue = queue  # pylint: disable=protected-access
        job = Job('j1', 'r1', 'alog', 'LS-1-m3.alog', 'ab' * 32, 10, 1.0)
        with patch.object(gate, 'QMessageBox') as box_cls:
            gate._on_upload_event('rejected', job, 'File không thuộc mẻ này')  # pylint: disable=protected-access
        box = box_cls.return_value
        assert 'LS-1-m3.alog' in box.setText.call_args.args[0]
        detail = box.setInformativeText.call_args.args[0]
        assert 'File không thuộc mẻ này' in detail and '/data/erp_upload_queue/rejected' in detail
        box.exec.assert_called_once()

    def test_events_before_anything_is_set_up_are_harmless(self) -> None:
        gate._on_upload_event('changed', None, '')  # pylint: disable=protected-access


class TestServerConfiguration:
    """A build without an ERP server never reaches the login: it says so and stays out."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> Generator[None, None, None]:
        gate._reset_state()  # pylint: disable=protected-access
        yield
        gate._reset_state()  # pylint: disable=protected-access

    @staticmethod
    def _unconfigured(monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_API_URL, raising=False)
        monkeypatch.delenv(config.ENV_ENVIRONMENT, raising=False)
        monkeypatch.setattr(config, '_config_file', lambda: __import__('pathlib').Path('/nonexistent/erp-config.json'))
        config.reset_cache()

    def test_a_build_without_a_server_is_refused_before_anything_is_asked(self, aw:MagicMock, session:MagicMock, monkeypatch:pytest.MonkeyPatch) -> None:
        self._unconfigured(monkeypatch)
        with patch.object(gate, 'show_config_error') as shown, patch.object(gate, 'erp_login') as login:
            assert gate.require_login(aw) is False
        shown.assert_called_once()
        assert 'chưa được cấu hình máy chủ ERP' in shown.call_args.args[0].message
        session.restore.assert_not_called()   # the stored token is not even looked up
        login.assert_not_called()

    def test_the_error_box_tells_what_is_wrong(self) -> None:
        with patch.object(gate, 'QMessageBox') as box_cls:
            gate.show_config_error(config.ConfigError('chưa cấu hình'))
        box = box_cls.return_value
        assert box.setInformativeText.call_args.args[0] == 'chưa cấu hình'
        box.exec.assert_called_once()

    def test_a_test_environment_is_in_the_title(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_ENVIRONMENT, 'staging')
        config.reset_cache()
        assert gate.window_title_suffix() == ' [STAGING]'

    def test_production_has_no_tag(self) -> None:
        assert gate.window_title_suffix() == ''

    def test_the_tag_comes_before_the_roaster(self, monkeypatch:pytest.MonkeyPatch, aw:MagicMock, roaster_env:SimpleNamespace) -> None:
        monkeypatch.setenv(config.ENV_ENVIRONMENT, 'staging')
        config.reset_cache()
        FakeDialog.script = [{'accept': True, 'claim': _claim(), 'roaster': _roaster()}]
        gate.require_roaster(aw)
        manager = roaster_env.manager_cls.return_value
        manager.state = LeaseState.HELD
        manager.roaster_name = 'Máy Rang Nhỏ (ROAST-01)'
        assert gate.window_title_suffix() == ' [STAGING] · Máy rang: Máy Rang Nhỏ (ROAST-01)'
