#!/usr/bin/python
#
# banner.py
#
# ABOUT
# A thin strip at the top of the main window: which roaster this session holds, and a loud warning if it does not any more.
# Text always says what is going on (colour is only an addition). Losing the roaster never discards the roast being recorded.

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

from typing import Final

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QBoxLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from erp.lease import LeaseState

_OBJECT_NAME: Final[str] = 'erpLeaseBanner'


def lost_text(name:str, reason:str) -> str:
    why = {
        'lease_lost': QApplication.translate('ERP', 'máy đã được phiên khác chiếm hoặc phiên của bạn đã hết hạn'),
        'force_released': QApplication.translate('ERP', 'quản trị viên đã ép nhả máy'),
        'session_expired': QApplication.translate('ERP', 'phiên đăng nhập ERP đã hết hạn'),
        'not_found': QApplication.translate('ERP', 'máy không còn hoạt động trong ERP'),
        'forbidden': QApplication.translate('ERP', 'tài khoản không còn quyền vận hành máy rang'),
    }.get(reason, QApplication.translate('ERP', 'ERP không còn xác nhận quyền giữ máy'))
    return QApplication.translate(
        'ERP', 'Bạn không còn giữ {0}: {1}. Dữ liệu đang ghi trên máy tính này vẫn được giữ nguyên. Hãy chọn lại máy trước khi tiếp tục điều khiển máy rang.'
    ).format(name, why)


def _stylesheet(palette:QPalette) -> str:
    dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
    held_bg, held_fg = ('#1d2b3a', '#b8d4f0') if dark else ('#e8f1fb', '#0f3f73')
    warn_bg, warn_fg = ('#4a3510', '#ffd37a') if dark else ('#fdf0d5', '#7a4a00')
    lost_bg, lost_fg = ('#4d1f1f', '#ffc9c2') if dark else ('#fdecea', '#8a1c1c')
    return f"""
        QFrame#{_OBJECT_NAME} {{ border: none; }}
        QFrame#{_OBJECT_NAME}[kind="held"] {{ background: {held_bg}; }}
        QFrame#{_OBJECT_NAME}[kind="suspect"] {{ background: {warn_bg}; }}
        QFrame#{_OBJECT_NAME}[kind="lost"] {{ background: {lost_bg}; }}
        QFrame#{_OBJECT_NAME}[kind="held"] QLabel {{ color: {held_fg}; }}
        QFrame#{_OBJECT_NAME}[kind="suspect"] QLabel {{ color: {warn_fg}; font-weight: 600; }}
        QFrame#{_OBJECT_NAME}[kind="lost"] QLabel {{ color: {lost_fg}; font-weight: 600; }}
        QFrame#{_OBJECT_NAME} QPushButton {{
            padding: 4px 12px; border-radius: 5px; border: 1px solid {lost_fg}; background: transparent; color: {lost_fg}; font-weight: 600;
        }}
        QFrame#{_OBJECT_NAME} QPushButton:hover {{ background: {lost_fg}; color: {lost_bg}; }}
    """


class LeaseBanner(QFrame):

    reselect = pyqtSignal()

    def __init__(self, parent:QWidget|None = None) -> None:
        super().__init__(parent)
        self.setObjectName(_OBJECT_NAME)
        self.kind:str = 'held'
        self.label:QLabel = QLabel('')
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setAccessibleName(QApplication.translate('ERP', 'Trạng thái máy rang'))
        self.reselect_button:QPushButton = QPushButton(QApplication.translate('ERP', 'Chọn lại máy'))
        self.reselect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reselect_button.clicked.connect(self.reselect.emit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.reselect_button)
        self.setStyleSheet(_stylesheet(self.palette()))
        self.setProperty('kind', self.kind)
        self.setVisible(False)

    def _set_kind(self, kind:str) -> None:
        self.kind = kind
        self.setProperty('kind', kind)
        # the stylesheet keys on the frame's property but colours its children: all of them must be restyled
        for widget in (self, *self.findChildren(QWidget)):
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)

    def apply(self, state:LeaseState, name:str, reason:str) -> None:
        """Shows the strip for a lease state; IDLE hides it."""
        if state is LeaseState.IDLE:
            self.setVisible(False)
            return
        if state is LeaseState.HELD:
            self._set_kind('held')
            self.label.setText(QApplication.translate('ERP', 'Máy rang: {0}').format(name))
            self.reselect_button.setVisible(False)
        elif state is LeaseState.SUSPECT:
            self._set_kind('suspect')
            self.label.setText(QApplication.translate(
                'ERP', 'Mất kết nối tới ERP, {0} có thể bị người khác chiếm. Đang thử kết nối lại…').format(name))
            self.reselect_button.setVisible(False)
        else:
            self._set_kind('lost')
            self.label.setText(lost_text(name, reason))
            self.reselect_button.setVisible(True)
        self.setVisible(True)


def install(host:QWidget) -> LeaseBanner:
    """Puts the banner on top of the host's layout (once)."""
    existing:LeaseBanner|None = host.findChild(LeaseBanner) # the stubs claim it is never None, it is when nothing was installed yet
    if existing is not None:
        return existing
    b = LeaseBanner(host)
    layout = host.layout()
    if isinstance(layout, QBoxLayout):
        layout.insertWidget(0, b)
    return b
