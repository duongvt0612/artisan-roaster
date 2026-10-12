"""Unit tests for erp.login: the ERP login dialog (real Qt widgets, offscreen)."""

import os
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit # noqa: E402

from erp import client, login # noqa: E402


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    app = QApplication.instance() or QApplication([])
    yield app # type:ignore[misc]


@pytest.fixture
def dialog(qapp:QApplication) -> Generator[login.ErpLogin, None, None]: # noqa: ARG001
    d = login.ErpLogin(None, MagicMock())
    yield d
    d.deleteLater()


def _wait(qapp:QApplication, condition:Any, timeout:float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


class TestInputValidation:
    @pytest.mark.parametrize(('username', 'password', 'valid'), [
        ('admin', 'Admin@12345', True),
        ('abc', '123456', True),
        ('ab', '123456', False),            # username too short
        ('a'*31, '123456', False),          # username too long
        ('ad min', '123456', False),        # space not allowed
        ('adm!n', '123456', False),         # only letters, digits and underscore
        ('admin', '12345', False),          # password too short
        ('', '', False),
    ])
    def test_is_input_reasonable(self, username:str, password:str, valid:bool) -> None:
        assert login.is_input_reasonable(username, password) is valid


class TestErrorText:
    def test_transient_errors_get_a_local_message(self, qapp:QApplication) -> None: # noqa: ARG002
        for kind in ('network', 'rate_limited', 'server'):
            text = login.error_text(client.ErpAuthError(kind, 'raw'))  # type:ignore[arg-type]
            assert text and text != 'raw'

    def test_rejections_show_the_server_message(self, qapp:QApplication) -> None: # noqa: ARG002
        for kind in ('invalid_credentials', 'disabled', 'invalid_session'):
            assert login.error_text(client.ErpAuthError(kind, 'Tài khoản đã bị khóa')) == 'Tài khoản đã bị khóa'  # type:ignore[arg-type]


class TestDialog:
    def test_ok_is_disabled_until_the_input_is_valid(self, dialog:login.ErpLogin) -> None:
        assert dialog.ok_button is not None
        assert not dialog.ok_button.isEnabled()
        dialog.textName.setText('admin')
        assert not dialog.ok_button.isEnabled()
        dialog.textPass.setText('Admin@12345')
        assert dialog.ok_button.isEnabled()
        dialog.textPass.setText('x')
        assert not dialog.ok_button.isEnabled()

    def test_prefilled_username(self, qapp:QApplication) -> None: # noqa: ARG002
        d = login.ErpLogin(None, MagicMock(), username='admin')
        assert d.textName.text() == 'admin'
        d.deleteLater()

    def test_password_field_is_masked(self, dialog:login.ErpLogin) -> None:
        assert dialog.textPass.echoMode() == QLineEdit.EchoMode.Password

    def test_successful_login_accepts_the_dialog_and_passes_the_remember_flag(self, qapp:QApplication, dialog:login.ErpLogin) -> None:
        dialog.textName.setText('admin')
        dialog.textPass.setText('Admin@12345')
        dialog.rememberCheckbox.setChecked(False)
        with patch.object(login, 'session') as session_mock:
            session_login = session_mock.login
            session_login.return_value = {}

            dialog._submit()  # pylint: disable=protected-access
            _wait(qapp, lambda: dialog.result() == QDialog.DialogCode.Accepted)
        session_login.assert_called_once_with('admin', 'Admin@12345', False)
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.username == 'admin'
        assert dialog.textPass.text() == ''  # the password does not stay in the widget

    def test_failed_login_shows_the_error_and_stays_open(self, qapp:QApplication, dialog:login.ErpLogin) -> None:
        dialog.textName.setText('admin')
        dialog.textPass.setText('wrong-pass')
        error = client.ErpAuthError('invalid_credentials', 'Tên đăng nhập hoặc mật khẩu không đúng')
        with patch.object(login, 'session') as session_mock:
            session_mock.login.side_effect = error

            dialog._submit()  # pylint: disable=protected-access
            _wait(qapp, lambda: not dialog.busy)
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.errorLabel.text() == 'Tên đăng nhập hoặc mật khẩu không đúng'
        assert not dialog.errorLabel.isHidden()
        assert dialog.textName.isEnabled()
        assert dialog.ok_button is not None
        assert dialog.ok_button.isEnabled()  # the user can fix the password and retry

    def test_inputs_are_locked_while_the_request_runs(self, qapp:QApplication, dialog:login.ErpLogin) -> None:
        dialog.textName.setText('admin')
        dialog.textPass.setText('Admin@12345')
        release = __import__('threading').Event()

        def slow_login(*_:Any) -> dict[str, Any]:
            release.wait(3)
            return {}

        with patch.object(login, 'session') as session_mock:
            session_mock.login.side_effect = slow_login

            dialog._submit()  # pylint: disable=protected-access
            assert dialog.busy
            assert dialog.ok_button is not None
            assert not dialog.ok_button.isEnabled()
            assert not dialog.textName.isEnabled()
            release.set()
            _wait(qapp, lambda: not dialog.busy)

    def test_unexpected_exception_is_reported_as_a_server_error(self, qapp:QApplication, dialog:login.ErpLogin) -> None:
        dialog.textName.setText('admin')
        dialog.textPass.setText('Admin@12345')
        with patch.object(login, 'session') as session_mock:
            session_mock.login.side_effect = RuntimeError('boom')

            dialog._submit()  # pylint: disable=protected-access
            _wait(qapp, lambda: not dialog.busy)
        assert not dialog.busy
        assert dialog.errorLabel.text()

    def test_closing_while_busy_ignores_the_late_result(self, qapp:QApplication, dialog:login.ErpLogin) -> None:
        dialog.textName.setText('admin')
        dialog.textPass.setText('Admin@12345')
        release = __import__('threading').Event()

        def slow_login(*_:Any) -> dict[str, Any]:
            release.wait(3)
            return {}

        with patch.object(login, 'session') as session_mock:
            session_mock.login.side_effect = slow_login

            dialog._submit()  # pylint: disable=protected-access
            dialog.reject()
            release.set()
            time.sleep(0.1)
            qapp.processEvents()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.username is None


class TestErpLogin:
    def test_returns_false_if_the_dialog_is_rejected(self, qapp:QApplication) -> None: # noqa: ARG002
        with patch.object(login.ErpLogin, 'exec', return_value=0):
            assert login.erp_login(MagicMock()) is False

    def test_returns_true_only_if_accepted_and_the_session_is_authenticated(self, qapp:QApplication) -> None: # noqa: ARG002
        with patch.object(login.ErpLogin, 'exec', return_value=1), patch.object(login, 'session') as session:
            session.is_authenticated = True
            assert login.erp_login(MagicMock()) is True
            session.is_authenticated = False
            assert login.erp_login(MagicMock()) is False
