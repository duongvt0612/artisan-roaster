"""Unit tests for erp.gate: the mandatory ERP login before the main window is shown."""

from unittest.mock import MagicMock, patch

import pytest

from erp import client, gate


@pytest.fixture
def aw() -> MagicMock:
    return MagicMock()


@pytest.fixture
def session() -> MagicMock:
    with patch.object(gate, 'session') as s:
        s.is_authenticated = False
        s.stored_username.return_value = 'admin'
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


def _roaster(rid:str = 'eq1', name:str = 'Máy Rang Nhỏ') -> SimpleNamespace:
    return SimpleNamespace(id=rid, name=name, code='ROAST-01')


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
         patch.object(gate.erp_banner, 'install') as install:
        install.return_value = MagicMock()
        yield SimpleNamespace(manager_cls=manager_cls, install=install)
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
