#!/usr/bin/python
#
# gate.py
#
# ABOUT
# The mandatory ERP login: the main window is only shown if require_login() returned True.
# There is deliberately no offline mode, if the ERP cannot be reached the user cannot enter.

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

import logging
from typing import Final, TYPE_CHECKING

from PyQt6.QtWidgets import QApplication, QMessageBox

from erp.client import ErpAuthError
from erp.login import erp_login, error_text
from erp.session import session

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)


def ask_retry(error:ErpAuthError) -> bool:
    """Tells the user the ERP could not be reached. Returns True if he wants to try again."""
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle('ERP')
    box.setText(QApplication.translate('ERP', 'Không thể xác thực với ERP'))
    box.setInformativeText(error_text(error))
    retry = box.addButton(QApplication.translate('ERP', 'Thử lại'), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QApplication.translate('ERP', 'Thoát'), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(retry)
    box.exec()
    return box.clickedButton() is retry


def confirm_logout(aw:'ApplicationWindow') -> bool:
    box = QMessageBox(aw)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle('ERP')
    box.setText(QApplication.translate('ERP', 'Đăng xuất khỏi ERP và thoát Artisan?'))
    box.setInformativeText(QApplication.translate(
        'ERP', 'Tài khoản này cũng sẽ bị đăng xuất khỏi các thiết bị khác đang dùng ERP.'))
    yes = box.addButton(QApplication.translate('ERP', 'Đăng xuất'), QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QApplication.translate('ERP', 'Huỷ'), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is yes


def require_login(aw:'ApplicationWindow') -> bool:
    """Returns True only if the user is logged in to the ERP."""
    while True:
        try:
            if session.restore():
                return True
            break
        except ErpAuthError as e:
            # network down, ERP failing or rate limited: the stored session is kept, but the user stays out until it is verified
            _log.info('ERP session could not be verified: %s', e.kind)
            if not ask_retry(e):
                return False
    return erp_login(aw, session.stored_username())


def logout(aw:'ApplicationWindow') -> bool:
    """Ends the ERP session after confirmation. Returns True if logged out; the caller is expected to quit the application."""
    if not confirm_logout(aw):
        return False
    session.logout()
    return True
