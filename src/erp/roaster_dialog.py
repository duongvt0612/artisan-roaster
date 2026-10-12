#!/usr/bin/python
#
# roaster_dialog.py
#
# ABOUT
# The mandatory "choose your roaster" dialog shown right after the ERP login. The list comes from the ERP with the live occupation
# of every roaster; choosing one claims it atomically on the ERP (only one session may hold a roaster at any time).
# Closing the dialog without a roaster ends Artisan, there is no way in without one.
#
# All network calls run in background threads so that the dialog never freezes (the ERP timeouts are up to 20 seconds).

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
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final, TYPE_CHECKING, override

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget)

from artisanlib.dialogs import ArtisanDialog
from erp import config, device, roasters
from erp.login import build_stylesheet
from erp.roasters import Claim, Roaster

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)

# what the badge of a row says; the colour is only an addition, the words carry the meaning
_FREE:Final[str] = 'free'
_MINE:Final[str] = 'mine'
_BUSY:Final[str] = 'busy'


def badge_kind(r:Roaster) -> str:
    if r.is_free:
        return _FREE
    return _MINE if r.mine else _BUSY


def badge_text(kind:str) -> str:
    if kind == _FREE:
        return QApplication.translate('ERP', '● Trống')
    if kind == _MINE:
        return QApplication.translate('ERP', '● Bạn đang giữ')
    return QApplication.translate('ERP', '● Đang dùng')


def format_since(since:datetime|None, now:datetime|None = None) -> str:
    """'14:05' for today, '12/10 14:05' for an earlier day (local time)."""
    if since is None:
        return ''
    local = since.astimezone()
    today = (now or datetime.now().astimezone()).date()
    return local.strftime('%H:%M') if local.date() == today else local.strftime('%d/%m %H:%M')


def holder_text(r:Roaster) -> str:
    """'Nguyễn A · PC-1 · từ 14:05' for an occupied roaster, '' for a free one."""
    h = r.held_by
    if r.is_free or h is None:
        return ''
    parts = [h.full_name or QApplication.translate('ERP', 'Người dùng khác')]
    if h.device_name:
        parts.append(h.device_name)
    since = format_since(h.since)
    if since:
        parts.append(QApplication.translate('ERP', 'từ {0}').format(since))
    return ' · '.join(parts)


def sub_text(r:Roaster) -> str:
    parts = [r.code]
    if r.capacity:
        parts.append(f'{r.capacity} {r.capacity_unit}'.strip() if r.capacity_unit else r.capacity)
    return ' · '.join(p for p in parts if p)


def preselect_id(machines:list[Roaster], last_id:str|None) -> str|None:
    """The roaster used last time if it is free, else the first free one, else nothing."""
    free = [m for m in machines if m.is_free]
    if last_id and any(m.id == last_id for m in free):
        return last_id
    return free[0].id if free else None


def _badge_css(palette:QPalette) -> str:
    dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
    free_bg, free_fg = ('#1d3b27', '#9be3b0') if dark else ('#e3f4e8', '#14532d')
    busy_bg, busy_fg = ('#4a3510', '#ffd37a') if dark else ('#fdf0d5', '#7a4a00')
    mine_bg, mine_fg = ('#16324f', '#9cc9f5') if dark else ('#e1eefb', '#0f3f73')
    return f"""
        QLabel#erpBadge {{ border-radius: 10px; padding: 3px 10px; font-weight: 600; }}
        QLabel#erpBadge[kind="{_FREE}"] {{ background: {free_bg}; color: {free_fg}; }}
        QLabel#erpBadge[kind="{_BUSY}"] {{ background: {busy_bg}; color: {busy_fg}; }}
        QLabel#erpBadge[kind="{_MINE}"] {{ background: {mine_bg}; color: {mine_fg}; }}
        QLabel#erpRowName {{ font-size: 15px; font-weight: 600; }}
        QListWidget {{ border-radius: 6px; padding: 2px; outline: 0; }}
        QListWidget::item {{ border-radius: 6px; padding: 2px; }}
        QListWidget::item:selected {{ background: {'#16324f' if dark else '#dbeafe'}; }}
    """


class _Bridge(QObject):
    """Delivers the result of a background call to the GUI thread: (kind, result, error)."""
    done = pyqtSignal(str, object, object)
    refresh_requested = pyqtSignal()


class RoasterDialog(ArtisanDialog):

    __slots__ = [
        'user_name', 'can_force_release', 'claim', 'roaster', 'need_login', 'machines', 'busy', 'list',
        'errorLabel', 'statusLabel', 'ok_button', 'cancel_button', 'refresh_button', 'force_button',
        '_api', '_session_id', '_device_id', '_device_name', '_store', '_confirm_force', '_bridge', '_timer',
        '_first_load', '_pending', '_chosen', '_error_source',
    ]

    def __init__(
        self,
        parent:QWidget|None,
        aw:'ApplicationWindow',
        *,
        user_name:str,
        can_force_release:bool,
        api:Any = roasters,
        session_id:str|None = None,
        device_id:str|None = None,
        device_name:str|None = None,
        store:Any = None,
        confirm_force:Callable[[Roaster], tuple[bool, str]]|None = None,
        auto_refresh:bool = True,
    ) -> None:
        super().__init__(parent, aw)
        # the results are read after exec() returned
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.user_name:str = user_name
        self.can_force_release:bool = can_force_release
        self.claim:Claim|None = None
        self.roaster:Roaster|None = None
        self.need_login:bool = False
        self.machines:list[Roaster] = []
        self.busy:bool = False
        self._first_load:bool = True
        self._pending:int = 0
        self._chosen:Roaster|None = None
        self._error_source:str = ''  # which action produced the visible error: 'list' errors clear themselves on the next good load
        self._api:Any = api
        self._session_id:str = session_id or device.session_id()
        self._device_id:str = device_id or device.device_id(store)
        self._device_name:str = device_name or device.device_name()
        self._store:Any = store
        self._confirm_force:Callable[[Roaster], tuple[bool, str]] = confirm_force or self._ask_force
        self._bridge:_Bridge = _Bridge(self)
        self._bridge.done.connect(self._on_done)
        self._bridge.refresh_requested.connect(self.refresh)

        self.setStyleSheet(build_stylesheet(self.palette()) + _badge_css(self.palette()))
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        self.setWindowTitle(QApplication.translate('ERP', 'Chọn máy rang'))

        title = QLabel(QApplication.translate('ERP', 'Chọn máy rang'))
        title.setObjectName('erpTitle')
        subtitle = QLabel(QApplication.translate('ERP', 'Mỗi máy rang chỉ một phiên được dùng tại một thời điểm.'))
        subtitle.setObjectName('erpSubtitle')
        subtitle.setWordWrap(True)
        who = QLabel(QApplication.translate('ERP', 'Đăng nhập với tài khoản: {0}').format(user_name))
        who.setObjectName('erpHint')

        self.list = QListWidget()
        self.list.setAccessibleName(QApplication.translate('ERP', 'Danh sách máy rang'))
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.list.itemDoubleClicked.connect(lambda *_: self._choose())

        # loading / empty state, shown instead of the list rows
        self.statusLabel = QLabel('')
        self.statusLabel.setObjectName('erpSubtitle')
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.errorLabel = QLabel('')
        self.errorLabel.setObjectName('erpError')
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setAccessibleName(QApplication.translate('ERP', 'Lỗi'))
        self.errorLabel.setVisible(False)

        self.ok_button = QPushButton(QApplication.translate('ERP', 'Chọn máy này'))
        self.ok_button.setObjectName('erpPrimary')
        self.ok_button.setDefault(True)
        self.ok_button.setAutoDefault(True)
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self._choose)

        self.refresh_button = QPushButton(QApplication.translate('ERP', 'Làm mới'))
        self.refresh_button.setObjectName('erpToggle')
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.clicked.connect(self.refresh)

        self.force_button = QPushButton(QApplication.translate('ERP', 'Ép nhả máy này…'))
        self.force_button.setObjectName('erpToggle')
        self.force_button.setAutoDefault(False)
        self.force_button.setVisible(can_force_release)
        self.force_button.setEnabled(False)
        self.force_button.clicked.connect(self._force)

        self.cancel_button = QPushButton(QApplication.translate('ERP', 'Thoát Artisan'))
        self.cancel_button.setObjectName('erpSecondary')
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(self.refresh_button)
        tools.addWidget(self.force_button)
        tools.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(0)
        layout.addWidget(title)
        layout.addSpacing(2)
        layout.addWidget(subtitle)
        layout.addSpacing(2)
        layout.addWidget(who)
        layout.addSpacing(14)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.statusLabel)
        layout.addSpacing(8)
        layout.addLayout(tools)
        layout.addSpacing(8)
        layout.addWidget(self.errorLabel)
        layout.addSpacing(12)
        layout.addWidget(self.ok_button)
        layout.addSpacing(4)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setTabOrder(self.list, self.refresh_button)
        self.setTabOrder(self.refresh_button, self.force_button)
        self.setTabOrder(self.force_button, self.ok_button)
        self.setTabOrder(self.ok_button, self.cancel_button)

        self._timer:QTimer = QTimer(self)
        self._timer.setInterval(config.roaster_poll_seconds * 1000)
        self._timer.timeout.connect(self._poll)
        if auto_refresh:
            self._timer.start()
        QTimer.singleShot(0, self.refresh)

    # ----- background work -------------------------------------------------------------------------------------------------

    def _run(self, kind:str, work:Callable[[], Any]) -> None:
        self._pending += 1
        bridge = self._bridge

        def target() -> None:
            result:Any = None
            error:Any = None
            try:
                result = work()
            except Exception as e: # pylint: disable=broad-except
                error = e
            try:
                bridge.done.emit(kind, result, error)
            except RuntimeError:
                pass # the dialog is gone already

        threading.Thread(target=target, name=f'erp-roaster-{kind}', daemon=True).start()

    def request_refresh(self) -> None:
        """Thread-safe: asks the dialog to reload the list (called from the realtime thread)."""
        try:
            self._bridge.refresh_requested.emit()
        except RuntimeError:
            pass

    @pyqtSlot()
    def _poll(self) -> None:
        if not self.busy:
            self.refresh()

    @pyqtSlot()
    @pyqtSlot(bool)
    def refresh(self, _:bool = False) -> None:
        if self.busy:
            return
        if self._first_load:
            self.statusLabel.setText(QApplication.translate('ERP', 'Đang tải danh sách máy rang…'))
        self._run('list', lambda: self._api.list_roasters(self._session_id))

    def _set_busy(self, busy:bool) -> None:
        self.busy = busy
        self.list.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self._update_buttons()

    # ----- results ---------------------------------------------------------------------------------------------------------

    @pyqtSlot(str, object, object)
    def _on_done(self, kind:str, result:Any, error:Any) -> None:
        self._pending = max(0, self._pending - 1)
        if kind == 'list':
            self._on_listed(result, error)
        elif kind == 'claim':
            self._on_claimed(result, error)
        elif kind == 'force':
            self._set_busy(False)
            if error is None:
                self.show_error('')
            else:
                self._show_roaster_error(error)
            self.refresh()

    def _on_listed(self, machines:Any, error:Any) -> None:
        if error is not None:
            self._first_load = False
            self.statusLabel.setText('')
            self._show_roaster_error(error, 'list')
            return
        self._first_load = False
        self.machines = list(machines)
        self._fill_list()

    def _fill_list(self) -> None:
        current = self._current_id()
        wanted = current or (preselect_id(self.machines, device.last_roaster_id(self._store)) if self.list.count() == 0 else None)
        self.list.blockSignals(True)
        self.list.clear()
        for r in self.machines:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r)
            widget = self._row_widget(r)
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.AccessibleTextRole, self._accessible_text(r))
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
            if r.id == wanted:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if not self.machines:
            self.statusLabel.setText(QApplication.translate(
                'ERP', 'Chưa có máy rang nào đang hoạt động trong ERP. Hãy liên hệ quản trị viên.'))
        else:
            self.statusLabel.setText('')
        if self._error_source == 'list':
            self.show_error('')
        self._update_buttons()

    def _row_widget(self, r:Roaster) -> QWidget:
        w = QWidget()
        w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        name = QLabel(r.name or r.code)
        name.setObjectName('erpRowName')
        sub = QLabel(sub_text(r))
        sub.setObjectName('erpHint')
        left = QVBoxLayout()
        left.setSpacing(1)
        left.addWidget(name)
        left.addWidget(sub)
        holder = holder_text(r)
        if holder:
            who = QLabel(holder)
            who.setObjectName('erpHint')
            left.addWidget(who)
        kind = badge_kind(r)
        badge = QLabel(badge_text(kind))
        badge.setObjectName('erpBadge')
        badge.setProperty('kind', kind)
        row = QHBoxLayout(w)
        row.setContentsMargins(10, 8, 10, 8)
        row.addLayout(left, 1)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    @staticmethod
    def _accessible_text(r:Roaster) -> str:
        text = f'{r.name or r.code}, {sub_text(r)}, {badge_text(badge_kind(r)).lstrip("● ")}'
        holder = holder_text(r)
        return f'{text}, {holder}' if holder else text

    def _current(self) -> Roaster|None:
        item = self.list.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return data if isinstance(data, Roaster) else None

    def _current_id(self) -> str|None:
        r = self._current()
        return r.id if r is not None else None

    def _update_buttons(self) -> None:
        r = self._current()
        free_or_mine = r is not None and (r.is_free or r.mine)
        self.ok_button.setEnabled(not self.busy and free_or_mine)
        self.force_button.setEnabled(not self.busy and r is not None and not r.is_free and not r.held_by_session)

    def show_error(self, text:str, source:str = '') -> None:
        self.errorLabel.setText(text)
        self.errorLabel.setVisible(bool(text))
        self._error_source = source if text else ''

    def _show_roaster_error(self, error:Any, source:str = '') -> None:
        if isinstance(error, roasters.SessionExpired):
            self.need_login = True
            self.reject()
            return
        if isinstance(error, roasters.RoasterForbidden):
            self.show_error(QApplication.translate(
                'ERP', 'Tài khoản này chưa được cấp quyền vận hành máy rang. Hãy liên hệ quản trị viên để được cấp quyền “roasters:operate”.'), source)
        elif isinstance(error, roasters.RoasterUnavailable):
            self.show_error(QApplication.translate('ERP', 'Không kết nối được tới ERP. Kiểm tra mạng rồi bấm “Làm mới”.')
                            if error.kind == 'network' else
                            QApplication.translate('ERP', 'ERP đang bận hoặc gặp sự cố. Vui lòng thử lại sau ít giây.'), source)
        elif isinstance(error, roasters.RoasterError):
            self.show_error(error.message, source)
        else:
            _log.exception(error)
            self.show_error(QApplication.translate('ERP', 'Có lỗi không mong đợi: {0}').format(str(error)), source)

    # ----- choose / force release ------------------------------------------------------------------------------------------

    @pyqtSlot()
    def _choose(self) -> None:
        r = self._current()
        if self.busy or r is None or not (r.is_free or r.mine):
            return
        self.show_error('')
        self._set_busy(True)
        self.ok_button.setText(QApplication.translate('ERP', 'Đang chiếm máy…'))
        self._chosen = r
        self._run('claim', lambda: self._api.claim(
            r.id, session_id=self._session_id, device_id=self._device_id, device_name=self._device_name))

    def _on_claimed(self, claim:Any, error:Any) -> None:
        self.ok_button.setText(QApplication.translate('ERP', 'Chọn máy này'))
        self._set_busy(False)
        if error is not None:
            self._show_roaster_error(error)
            self.refresh()  # shows who holds it now
            return
        self.claim = claim
        self.roaster = self._chosen
        if self._chosen is not None:
            device.set_last_roaster_id(self._chosen.id, self._store)
        self.accept()

    @pyqtSlot()
    def _force(self) -> None:
        r = self._current()
        if self.busy or r is None or r.is_free or not self.can_force_release:
            return
        confirmed, reason = self._confirm_force(r)
        if not confirmed:
            return
        self.show_error('')
        self._set_busy(True)
        self._run('force', lambda: self._api.force_release(r.id, reason=reason))

    def _ask_force(self, r:Roaster) -> tuple[bool, str]:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(QApplication.translate('ERP', 'Ép nhả máy rang'))
        box.setText(QApplication.translate('ERP', 'Ép nhả {0}?').format(r.name or r.code))
        box.setInformativeText(QApplication.translate(
            'ERP', '{0} sẽ mất quyền điều khiển máy này ngay lập tức. Chỉ làm khi người đó đã đi vắng hoặc máy tính của họ bị treo. Việc này được ghi nhật ký.'
        ).format(holder_text(r) or QApplication.translate('ERP', 'Người đang dùng')))
        yes = box.addButton(QApplication.translate('ERP', 'Ép nhả'), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QApplication.translate('ERP', 'Huỷ'), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not yes:
            return False, ''
        reason, ok = QInputDialog.getText(self, QApplication.translate('ERP', 'Lý do'),
                                          QApplication.translate('ERP', 'Lý do ép nhả (không bắt buộc):'))
        return (True, reason.strip()) if ok else (False, '')

    @override
    def reject(self) -> None:
        self._timer.stop()
        super().reject()

    @override
    def accept(self) -> None:
        self._timer.stop()
        super().accept()
