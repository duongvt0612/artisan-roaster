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
