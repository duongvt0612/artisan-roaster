#!/usr/bin/python
#
# login.py
#
# ABOUT
# Login dialog for the LVG ERP accounts. The network call runs in a background thread
# so that the dialog stays responsive (the ERP timeouts are up to 20 seconds).

# LICENSE
# This program or module is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published
# by the Free Software Foundation, either version 2 of the License, or
# version 3 of the License, or (at your option) any later version. It is
# provided for educational purposes and is distributed in the hope that
# it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
# the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import re
import logging
import threading
from typing import Any, Final, TYPE_CHECKING, override

from urllib.parse import urlparse

from PyQt6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget)
from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QKeySequence, QPalette

from artisanlib.dialogs import ArtisanDialog
from erp import config
from erp.client import ErpAuthError
from erp.session import session

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)

_USERNAME_RE: Final[re.Pattern[str]] = re.compile(
    rf'^[A-Za-z0-9_]{{{config.min_username_len},{config.max_username_len}}}$')


def is_input_reasonable(username:str, password:str) -> bool:
    """Mirrors the validation of the ERP login endpoint to save a round trip on obvious typos."""
    return bool(_USERNAME_RE.match(username)) and len(password) >= config.min_passwd_len


def error_text(error:ErpAuthError) -> str:
    """User facing text of a failed login. The ERP answers in Vietnamese, so its messages are shown as they are."""
    if error.kind == 'network':
        return QApplication.translate('ERP', 'Không kết nối được tới ERP. Kiểm tra mạng rồi thử lại.')
    if error.kind == 'rate_limited':
        return QApplication.translate('ERP', 'Thử quá nhiều lần. Vui lòng đợi một phút rồi thử lại.')
    if error.kind == 'server':
        return QApplication.translate('ERP', 'ERP đang gặp sự cố. Vui lòng thử lại sau.')
    return error.message


_ACCENT: Final[str] = '#2b6cb0' # the blue of the Artisan toolbar buttons, white text on it has a contrast of > 5:1


def _blend(a:QColor, b:QColor, t:float) -> str:
    """Mix of a (weight t) and b, as #rrggbb."""
    return QColor(
        round(a.red() * t + b.red() * (1 - t)),
        round(a.green() * t + b.green() * (1 - t)),
        round(a.blue() * t + b.blue() * (1 - t))).name()


def build_stylesheet(palette:QPalette) -> str:
    """Stylesheet derived from the palette of the running style, so that it follows the light/dark mode of the system."""
    window = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.WindowText)
    base = palette.color(QPalette.ColorRole.Base)
    dark = window.lightness() < 128
    muted = _blend(text, window, 0.7)        # secondary text, > 4.5:1 on the window color
    border = _blend(text, base, 0.35)        # input borders, > 3:1 on the input background
    focus = '#6aa9e9' if dark else _ACCENT
    error_bg, error_fg, error_border = ('#3d1f1f', '#ffb4ab', '#8c3a3a') if dark else ('#fdecea', '#9b1c1c', '#e8a09a')
    disabled_bg = _blend(text, window, 0.25)
    return f"""
        QLabel#erpTitle {{ font-size: 22px; font-weight: 600; }}
        QLabel#erpSubtitle {{ color: {muted}; }}
        QLabel#erpFieldLabel {{ font-weight: 600; }}
        QLabel#erpHint {{ color: {muted}; font-size: 12px; }}
        QLineEdit {{
            min-height: 22px; padding: 7px 10px; border-radius: 6px;
            border: 1px solid {border}; background: {palette.color(QPalette.ColorRole.Base).name()};
            selection-background-color: {_ACCENT}; selection-color: #ffffff;
        }}
        QLineEdit:focus {{ border: 2px solid {focus}; padding: 6px 9px; }}
        QLineEdit:disabled {{ color: {muted}; }}
        QPushButton#erpToggle {{
            min-height: 22px; min-width: 44px; padding: 7px 8px; border-radius: 6px;
            border: 1px solid {border}; background: transparent;
        }}
        QPushButton#erpToggle:focus {{ border: 2px solid {focus}; padding: 6px 7px; }}
        QPushButton#erpToggle:checked {{ border: 1px solid {focus}; color: {focus}; font-weight: 600; }}
        QPushButton#erpPrimary {{
            min-height: 24px; padding: 8px 16px; border-radius: 6px; border: none;
            background: {_ACCENT}; color: #ffffff; font-weight: 600;
        }}
        QPushButton#erpPrimary:hover {{ background: #2a5f9a; }}
        QPushButton#erpPrimary:pressed {{ background: #234f80; }}
        QPushButton#erpPrimary:focus {{ border: 2px solid {focus}; padding: 6px 14px; }}
        QPushButton#erpPrimary:disabled {{ background: {disabled_bg}; color: {muted}; }}
        QPushButton#erpSecondary {{
            min-height: 24px; padding: 6px 12px; border: none; background: transparent; color: {muted};
        }}
        QPushButton#erpSecondary:hover {{ color: {_blend(text, window, 1.0)}; text-decoration: underline; }}
        QPushButton#erpSecondary:focus {{ border: 2px solid {focus}; border-radius: 6px; padding: 4px 10px; }}
        QLabel#erpError {{
            background: {error_bg}; color: {error_fg}; border: 1px solid {error_border};
            border-radius: 6px; padding: 8px 10px;
        }}
    """


class _Bridge(QObject):
    """Delivers the result of the background call to the GUI thread."""
    succeeded = pyqtSignal()
    failed = pyqtSignal(object)


class ErpLogin(ArtisanDialog):

    __slots__ = [
        'username',
        'remember',
        'textName',
        'textPass',
        'rememberCheckbox',
        'showPassButton',
        'errorLabel',
        'titleLabel',
        'subtitleLabel',
        'ok_button',
        'cancel_button',
        'busy',
        '_bridge',
        '_ok_text',
    ]

    def __init__(
        self,
        parent:QWidget|None,
        aw:'ApplicationWindow',
        username:str|None = None,
        remember:bool = True,
    ) -> None:
        super().__init__(parent, aw)
        # the results are read after exec() returned, so the dialog must not delete itself on close
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self.username:str|None = None
        self.remember:bool = remember
        self.busy:bool = False
        self._bridge:_Bridge = _Bridge(self)
        self._bridge.succeeded.connect(self._on_succeeded)
        self._bridge.failed.connect(self._on_failed)

        self.setStyleSheet(build_stylesheet(self.palette()))
        self.setMinimumWidth(400)

        # header: icon, title, subtitle
        icon_label = QLabel()
        icon = QApplication.windowIcon()
        if icon.isNull():
            icon_label.setVisible(False)
        else:
            icon_label.setPixmap(icon.pixmap(48, 48))
        self.titleLabel = QLabel(QApplication.translate('ERP', 'Đăng nhập'))
        self.titleLabel.setObjectName('erpTitle')
        self.subtitleLabel = QLabel(QApplication.translate('ERP', 'Dùng tài khoản ERP của LVG để vào Artisan'))
        self.subtitleLabel.setObjectName('erpSubtitle')
        self.subtitleLabel.setWordWrap(True)
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        header_text.addWidget(self.titleLabel)
        header_text.addWidget(self.subtitleLabel)
        header = QHBoxLayout()
        header.setSpacing(14)
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(header_text, 1)

        # fields, each with a visible label (not only a placeholder)
        self.textName:QLineEdit = QLineEdit(self)
        self.textName.setAccessibleName(QApplication.translate('ERP', 'Tên đăng nhập'))
        self.textName.setMaxLength(config.max_username_len)
        self.textName.setText(username or '')
        self.textName.textChanged.connect(self._on_text_changed)

        self.textPass:QLineEdit = QLineEdit(self)
        self.textPass.setEchoMode(QLineEdit.EchoMode.Password)
        self.textPass.setAccessibleName(QApplication.translate('ERP', 'Mật khẩu'))
        self.textPass.textChanged.connect(self._on_text_changed)

        self.showPassButton = QPushButton(QApplication.translate('ERP', 'Hiện'))
        self.showPassButton.setObjectName('erpToggle')
        self.showPassButton.setCheckable(True)
        self.showPassButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.showPassButton.setAccessibleName(QApplication.translate('ERP', 'Hiện mật khẩu'))
        self.showPassButton.toggled.connect(self._on_show_password_toggled)

        pass_row = QHBoxLayout()
        pass_row.setSpacing(8)
        pass_row.addWidget(self.textPass, 1)
        pass_row.addWidget(self.showPassButton)

        self.rememberCheckbox = QCheckBox(QApplication.translate('ERP', 'Ghi nhớ đăng nhập trên máy này'))
        self.rememberCheckbox.setChecked(remember)
        self.rememberCheckbox.toggled.connect(self._on_remember_toggled)

        # the error stays right below the fields and always carries its own message, not only a color
        self.errorLabel = QLabel('')
        self.errorLabel.setObjectName('erpError')
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setAccessibleName(QApplication.translate('ERP', 'Lỗi đăng nhập'))
        self.errorLabel.setVisible(False)

        # one primary action, the secondary one is visually subordinate
        self.ok_button = QPushButton(QApplication.translate('ERP', 'Đăng nhập'))
        self.ok_button.setObjectName('erpPrimary')
        self.ok_button.setDefault(True)
        self.ok_button.setAutoDefault(True)
        self.ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self._submit)
        self._ok_text:str = self.ok_button.text()

        self.cancel_button = QPushButton(QApplication.translate('ERP', 'Thoát Artisan'))
        self.cancel_button.setObjectName('erpSecondary')
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setShortcut(QKeySequence('Ctrl+.'))
        self.cancel_button.clicked.connect(self.reject)

        env_tag = config.environment_label()
        hint = QLabel(QApplication.translate('ERP', 'Máy chủ: {0}').format(urlparse(config.get_api_base_url()).netloc)
                      + (QApplication.translate('ERP', ' · MÔI TRƯỜNG {0}').format(env_tag) if env_tag else ''))
        hint.setObjectName('erpHint')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addSpacing(20)
        for text, field in (
            (QApplication.translate('ERP', 'Tên đăng nhập'), self.textName),
            (QApplication.translate('ERP', 'Mật khẩu'), None),
        ):
            label = QLabel(text)
            label.setObjectName('erpFieldLabel')
            if field is not None:
                label.setBuddy(field)
            else:
                label.setBuddy(self.textPass)
            layout.addWidget(label)
            layout.addSpacing(6)
            if field is not None:
                layout.addWidget(field)
            else:
                layout.addLayout(pass_row)
            layout.addSpacing(14)
        layout.addWidget(self.rememberCheckbox)
        layout.addSpacing(8)
        layout.addWidget(self.errorLabel)
        layout.addSpacing(16)
        layout.addWidget(self.ok_button)
        layout.addSpacing(4)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(8)
        layout.addWidget(hint)

        self.setTabOrder(self.textName, self.textPass)
        self.setTabOrder(self.textPass, self.showPassButton)
        self.setTabOrder(self.showPassButton, self.rememberCheckbox)
        self.setTabOrder(self.rememberCheckbox, self.ok_button)
        self.setTabOrder(self.ok_button, self.cancel_button)

        self._on_text_changed('')
        if username:
            self.textPass.setFocus()
        else:
            self.textName.setFocus()

    def _inputs_valid(self) -> bool:
        return is_input_reasonable(self.textName.text().strip(), self.textPass.text())

    def _set_busy(self, busy:bool) -> None:
        self.busy = busy
        for widget in (self.textName, self.textPass, self.rememberCheckbox, self.showPassButton):
            widget.setEnabled(not busy)
        self.ok_button.setText(QApplication.translate('ERP', 'Đang đăng nhập…') if busy else self._ok_text)
        self.ok_button.setEnabled(not busy and self._inputs_valid())

    def show_error(self, text:str) -> None:
        self.errorLabel.setText(text)
        self.errorLabel.setVisible(bool(text))

    @pyqtSlot(str)
    def _on_text_changed(self, _:str) -> None:
        if not self.busy:
            self.ok_button.setEnabled(self._inputs_valid())

    @pyqtSlot(bool)
    def _on_show_password_toggled(self, shown:bool) -> None:
        self.textPass.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        self.showPassButton.setText(QApplication.translate('ERP', 'Ẩn') if shown else QApplication.translate('ERP', 'Hiện'))
        self.showPassButton.setAccessibleName(
            QApplication.translate('ERP', 'Ẩn mật khẩu') if shown else QApplication.translate('ERP', 'Hiện mật khẩu'))

    @pyqtSlot(bool)
    def _on_remember_toggled(self, checked:bool) -> None:
        self.remember = checked

    @pyqtSlot()
    def _submit(self) -> None:
        if self.busy or not self._inputs_valid():
            return
        username = self.textName.text().strip()
        password = self.textPass.text()
        remember = self.remember
        self.show_error('')
        self._set_busy(True)
        bridge = self._bridge

        def work() -> None:
            try:
                session.login(username, password, remember)
                signal:Any = bridge.succeeded
                args:tuple[Any, ...] = ()
            except ErpAuthError as e:
                signal, args = bridge.failed, (e,)
            except Exception as e: # pylint: disable=broad-except
                _log.exception(e)
                signal, args = bridge.failed, (ErpAuthError('server', str(e)),)
            try:
                signal.emit(*args)
            except RuntimeError:
                pass # the dialog is gone already

        threading.Thread(target=work, name='erp-login', daemon=True).start()

    @pyqtSlot()
    def _on_succeeded(self) -> None:
        self.username = self.textName.text().strip()
        self.textPass.clear()
        self.showPassButton.setChecked(False)
        self._set_busy(False)
        self.accept()

    @pyqtSlot(object)
    def _on_failed(self, error:ErpAuthError) -> None:
        self._set_busy(False)
        self.textPass.selectAll()
        self.textPass.setFocus()
        self.show_error(error_text(error))

    @override
    def reject(self) -> None:
        # ignore a late result of a login that was still running when the dialog was closed
        self._bridge.blockSignals(True)
        super().reject()


def erp_login(aw:'ApplicationWindow', username:str|None = None, remember:bool = True) -> bool:
    """Shows the login dialog, returns True if the user is logged in to the ERP afterwards."""
    _log.debug('erp_login()')
    dialog = ErpLogin(None, aw, username, remember)
    dialog.setWindowTitle(QApplication.translate('ERP', 'Đăng nhập ERP'))
    try:
        return bool(dialog.exec()) and session.is_authenticated
    finally:
        dialog.deleteLater()
