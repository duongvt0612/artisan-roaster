#
# login.py
#
# Copyright (c) 2023, Paul Holleis, Marko Luther
# All rights reserved.
#
#
# ABOUT
# This module connects to the artisan.plus inventory management service

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

from PyQt6.QtWidgets import (QApplication, QCheckBox, QGroupBox, QHBoxLayout,
    QVBoxLayout, QLabel, QLineEdit, QDialogButtonBox, QWidget)
from PyQt6.QtCore import Qt, pyqtSlot, QUrl
from PyQt6.QtGui import QKeySequence, QAction, QDesktopServices

import logging
from artisanlib.dialogs import ArtisanDialog
from plus import config
from typing import override, Final, TYPE_CHECKING

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)

class Login(ArtisanDialog):

    __slots__ = [
        'login',
        'passwd',
        'remember',
        'textPass',
        'textName',
        'rememberCheckbox',
        'titleLabel',
        'subtitleLabel',
        'guideButton',
    ]


    def __init__(
        self,
        parent:QWidget,
        aw:'ApplicationWindow',
        email:str|None = None,
        saved_password:str|None = None,
        remember_credentials: bool = True,
    ) -> None:
        super().__init__(parent,aw)

        self.login:str|None = None
        self.passwd:str|None = None
        self.remember:bool = remember_credentials

        self.dialogbuttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.setButtonTranslations(
            self.dialogbuttons.button(QDialogButtonBox.StandardButton.Ok),
            'OK',
            QApplication.translate('Plus', 'Đăng nhập'),
        )
        self.setButtonTranslations(
            self.dialogbuttons.button(QDialogButtonBox.StandardButton.Cancel),
            'Cancel',
            QApplication.translate('Button', 'Cancel'),
        )

        self.dialogbuttons.accepted.connect(self.setCredentials)
        self.dialogbuttons.rejected.connect(self.reject)
        self.ok_button = self.dialogbuttons.button(QDialogButtonBox.StandardButton.Ok)
        if self.ok_button is not None:
            self.ok_button.setEnabled(False)
            self.ok_button.setFocusPolicy(
                Qt.FocusPolicy.StrongFocus
            )
        self.cancel_button = self.dialogbuttons.button(QDialogButtonBox.StandardButton.Cancel)
        if self.cancel_button is not None:
            self.cancel_button.setDefault(True)
            self.cancel_button.setShortcut(
                QKeySequence('Ctrl+.')
            )
            cancelAction:QAction = QAction(self)
            cancelAction.triggered.connect(self.reject)
            cancelAction.setShortcut(QKeySequence.StandardKey.Cancel)
            self.cancel_button.addActions(
                [cancelAction]
            )

        self.titleLabel = QLabel(
            QApplication.translate('Plus', 'Đăng nhập')
        )
        self.subtitleLabel = QLabel(
            QApplication.translate('Plus', 'Đăng nhập vào tài khoản của bạn')
        )

        self.textPass:QLineEdit = QLineEdit(self)
        self.textPass.setEchoMode(QLineEdit.EchoMode.Password)
        self.textPass.setPlaceholderText(
            QApplication.translate('Plus', 'Mật khẩu')
        )

        self.textName:QLineEdit = QLineEdit(self)
        self.textName.setPlaceholderText(
            QApplication.translate('Plus', 'Tên đăng nhập')
        )
        self.textName.textChanged.connect(self.textChanged)
        if email is not None:
            self.textName.setText(email)

        self.textPass.textChanged.connect(self.textChanged)

        self.rememberCheckbox = QCheckBox(
            QApplication.translate('Plus', 'Remember')
        )
        self.rememberCheckbox.setChecked(self.remember)
        self.rememberCheckbox.stateChanged.connect(self.rememberCheckChanged)

        self.guideButton = QLabel(
            f'<small><a href="{config.get_user_guide_url()}">{QApplication.translate('Plus', 'Xem hướng dẫn sử dụng')}</a></small>'
        )
        self.guideButton.setOpenExternalLinks(True)

        credentialsLayout:QVBoxLayout = QVBoxLayout(self)
        credentialsLayout.addWidget(self.titleLabel)
        credentialsLayout.addWidget(self.subtitleLabel)
        credentialsLayout.addWidget(self.textName)
        credentialsLayout.addWidget(self.textPass)
        credentialsLayout.addWidget(self.rememberCheckbox)

        credentialsGroup:QGroupBox = QGroupBox()
        credentialsGroup.setLayout(credentialsLayout)

        buttonLayout:QHBoxLayout = QHBoxLayout()
        buttonLayout.addStretch()
        buttonLayout.addWidget(self.dialogbuttons)
        buttonLayout.addStretch()

        guideLayout:QHBoxLayout = QHBoxLayout()
        guideLayout.addStretch()
        guideLayout.addWidget(self.guideButton)
        guideLayout.addStretch()

        layout:QVBoxLayout = QVBoxLayout(self)
        layout.addWidget(credentialsGroup)
        layout.addLayout(guideLayout)
        layout.addLayout(buttonLayout)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        if saved_password is not None:
            self.passwd = saved_password
            self.textPass.setText(self.passwd)
            if self.isInputReasonable():
                if self.cancel_button is not None:
                    self.cancel_button.setDefault(False)
                if self.ok_button is not None:
                    self.ok_button.setDefault(True)
                    self.ok_button.setEnabled(True)



    @pyqtSlot()
    @override
    def reject(self) -> None:
        login = self.textName.text().strip()
        self.login = login if login else None
        super().reject()

    @pyqtSlot(int)
    def rememberCheckChanged(self, i:int) -> None:
        self.remember = bool(i)

    def isInputReasonable(self) -> bool:
        login = self.textName.text().strip()
        passwd = self.textPass.text()
        return (
            len(passwd) >= config.min_passwd_len
            and bool(login)
        )

    @pyqtSlot(str)
    def textChanged(self, _:str) -> None:
        if self.isInputReasonable():
            if self.cancel_button is not None:
                self.cancel_button.setDefault(False)
            if self.ok_button is not None:
                self.ok_button.setDefault(True)
                self.ok_button.setEnabled(True)
        else:
            if self.cancel_button is not None:
                self.cancel_button.setDefault(True)
            if self.ok_button is not None:
                self.ok_button.setDefault(False)
                self.ok_button.setEnabled(False)

    @pyqtSlot()
    def setCredentials(self) -> None:
        login = self.textName.text().strip()
        self.login = login if login else None
        self.passwd = self.textPass.text()
        self.accept()


def plus_login(
    window: QWidget,
    aw: 'ApplicationWindow',
    email: str|None = None,
    saved_password: str|None = None,
    remember_credentials: bool = True
) -> tuple[str|None, str|None, bool, int]:
    _log.debug('plus_login()')
    ld = Login(window, aw, email, saved_password, remember_credentials)
    ld.setWindowTitle('plus')
    ld.setWindowFlags(Qt.WindowType.Sheet)
    ld.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    res:int = ld.exec()
    login_processed:str|None = ld.login.strip() if ld.login is not None else None
    if login_processed == '':
        login_processed = None
    return login_processed, ld.passwd, ld.remember, res
