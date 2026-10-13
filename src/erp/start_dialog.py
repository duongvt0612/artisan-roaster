#!/usr/bin/python
#
# start_dialog.py
#
# ABOUT
# The dialog shown when the operator presses START: he MUST choose the production command ("lệnh sản xuất") this roast belongs to,
# by typing to search and picking from the list. Every row says what the command produces and how many kg, so that he can check
# for himself that he roasts for the right command. Then he types the kg of green coffee he loaded. Only when the ERP has accepted
# the batch (it answers with the batch id) does the dialog close and Artisan starts recording.

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
from typing import Any, Final, TYPE_CHECKING, override

from PyQt6.QtCore import QLocale, QObject, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (QApplication, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget)

from artisanlib.dialogs import ArtisanDialog
from erp import device, roasters, rounds
from erp.login import build_stylesheet
from erp.rounds import RoastCommand, RoundInput, StartedRound

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS: Final[int] = 250
SEARCH_LIMIT: Final[int] = 20
MAX_LISTED_ORDERS: Final[int] = 2


def format_kg(value:float) -> str:
    """'120', '80,5', '0,125': up to 3 decimals without trailing zeros, in the decimal separator of the user's locale."""
    locale = QLocale()
    text = locale.toString(float(round(value, 3)), 'f', 3)
    sep = locale.decimalPoint()
    if sep in text:
        text = text.rstrip('0').rstrip(sep)
    return text


def command_title(c:RoastCommand) -> str:
    """'LS-1026-000123 · Arabica Cầu Đất rang · 120 kg': the command, what it produces, how many kg."""
    return f'{c.code} · {c.output_name} · {format_kg(c.target_kg)} kg'


def orders_text(c:RoastCommand) -> str:
    """'SO-7 (Khách A), SO-9 (Khách B) +1', or the request code if the command has no sale order."""
    if not c.sale_orders:
        return c.request_code
    shown = [f'{o.order_number} ({o.customer_name})' if o.customer_name else o.order_number for o in c.sale_orders[:MAX_LISTED_ORDERS]]
    more = len(c.sale_orders) - MAX_LISTED_ORDERS
    return ', '.join(shown) + (f' +{more}' if more > 0 else '')


def command_detail(c:RoastCommand) -> str:
    """The second line of a row: what is left to roast and where the command comes from."""
    parts = [QApplication.translate('ERP', 'Còn lại {0} kg').format(format_kg(c.remaining_kg))]
    source = orders_text(c)
    if source:
        parts.append(source)
    return ' · '.join(parts)


def command_badge(c:RoastCommand) -> tuple[str, str]:
    """(kind, text) of the badge of a row. The words carry the meaning, the colour is only an addition."""
    if c.is_test:
        return 'test', QApplication.translate('ERP', 'Mẻ thử')
    if c.in_progress:
        return 'progress', QApplication.translate('ERP', 'Đang thực hiện')
    return 'pending', QApplication.translate('ERP', 'Chờ thực hiện')


def accessible_text(c:RoastCommand) -> str:
    return f'{command_title(c)}, {command_detail(c)}, {command_badge(c)[1]}'


def _css(palette:QPalette) -> str:
    dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
    test_bg, test_fg = ('#4a3510', '#ffd37a') if dark else ('#fdf0d5', '#7a4a00')
    prog_bg, prog_fg = ('#16324f', '#9cc9f5') if dark else ('#e1eefb', '#0f3f73')
    pend_bg, pend_fg = ('#2d2d2d', '#d0d0d0') if dark else ('#ececec', '#3c3c3c')
    warn_bg, warn_fg = ('#4a3510', '#ffd37a') if dark else ('#fdf0d5', '#7a4a00')
    return f"""
        QLabel#erpBadge {{ border-radius: 10px; padding: 3px 10px; font-weight: 600; }}
        QLabel#erpBadge[kind="test"] {{ background: {test_bg}; color: {test_fg}; }}
        QLabel#erpBadge[kind="progress"] {{ background: {prog_bg}; color: {prog_fg}; }}
        QLabel#erpBadge[kind="pending"] {{ background: {pend_bg}; color: {pend_fg}; }}
        QLabel#erpRowName {{ font-size: 14px; font-weight: 600; }}
        QLabel#erpWarn {{ background: {warn_bg}; color: {warn_fg}; border-radius: 6px; padding: 6px 10px; }}
        QLabel#erpSection {{ font-weight: 600; }}
        QListWidget {{ border-radius: 6px; padding: 2px; outline: 0; }}
        QListWidget::item {{ border-radius: 6px; padding: 2px; }}
        QListWidget::item:selected {{ background: {'#16324f' if dark else '#dbeafe'}; }}
        QDoubleSpinBox {{ min-height: 22px; padding: 4px 8px; border-radius: 6px; }}
    """


class _Bridge(QObject):
    """Delivers the result of a background call to the GUI thread: (kind, sequence number, result, error)."""
    done = pyqtSignal(str, int, object, object)


class StartRoundDialog(ArtisanDialog):

    __slots__ = [
        'roaster_name', 'capacity_kg', 'started', 'command', 'input_lines', 'need_login', 'busy_with_round', 'lease_lost',
        'search_edit', 'results', 'statusLabel', 'detail', 'detailTitle', 'inputsBox', 'inputsLayout', 'totalLabel', 'warnLabel', 'errorLabel',
        'start_button', 'cancel_button', 'busy', '_api', '_equipment_id', '_session_id', '_version', '_bridge', '_timer', '_seq', '_spins',
        '_commands', '_wanted_id', '_debounce_ms', '_pending',
    ]

    def __init__(
        self,
        parent:QWidget|None,
        aw:'ApplicationWindow',
        *,
        roaster_name:str,
        equipment_id:str,
        version:int,
        capacity_kg:float|None = None,
        api:Any = rounds,
        session_id:str|None = None,
        debounce_ms:int = SEARCH_DEBOUNCE_MS,
    ) -> None:
        super().__init__(parent, aw)
        # the results are read after exec() returned
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.roaster_name:str = roaster_name
        self.capacity_kg:float|None = capacity_kg
        self.started:StartedRound|None = None             # set once the ERP accepted the batch
        self.command:RoastCommand|None = None             # the command of that batch
        self.input_lines:list[tuple[RoundInput, float]] = []  # what was declared: (green coffee, kg)
        self.need_login:bool = False
        self.busy_with_round:bool = False                 # the roaster already has an unfinished batch
        self.lease_lost:bool = False                      # this session does not hold the roaster any more
        self.busy:bool = False
        self._api:Any = api
        self._equipment_id:str = equipment_id
        self._session_id:str = session_id or device.session_id()
        self._version:int = version
        self._seq:int = 0
        self._spins:list[tuple[RoundInput, QDoubleSpinBox]] = []
        self._commands:list[RoastCommand] = []
        self._wanted_id:str|None = None
        self._pending:tuple[RoastCommand, list[tuple[RoundInput, float]]]|None = None
        self._debounce_ms:int = debounce_ms
        self._bridge:_Bridge = _Bridge(self)
        self._bridge.done.connect(self._on_done)

        self.setStyleSheet(build_stylesheet(self.palette()) + _css(self.palette()))
        self.setMinimumWidth(640)
        self.setMinimumHeight(620)
        self.setWindowTitle(QApplication.translate('ERP', 'Bắt đầu mẻ rang'))

        title = QLabel(QApplication.translate('ERP', 'Bắt đầu mẻ rang'))
        title.setObjectName('erpTitle')
        subtitle = QLabel(QApplication.translate('ERP', 'Chọn lệnh sản xuất cho mẻ rang này trên {0}. Kiểm tra kỹ thành phẩm và số kg trước khi bắt đầu.').format(roaster_name))
        subtitle.setObjectName('erpSubtitle')
        subtitle.setWordWrap(True)

        label = QLabel(QApplication.translate('ERP', 'Lệnh sản xuất'))
        label.setObjectName('erpFieldLabel')
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(QApplication.translate('ERP', 'Gõ mã lệnh, tên thành phẩm, mã đơn hoặc khách hàng để tìm…'))
        self.search_edit.setAccessibleName(QApplication.translate('ERP', 'Tìm lệnh sản xuất'))
        self.search_edit.setClearButtonEnabled(True)
        label.setBuddy(self.search_edit)

        self.results = QListWidget()
        self.results.setAccessibleName(QApplication.translate('ERP', 'Danh sách lệnh sản xuất'))
        self.results.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.setMinimumHeight(190)
        self.results.currentItemChanged.connect(lambda *_: self._on_selected())

        self.statusLabel = QLabel('')
        self.statusLabel.setObjectName('erpHint')
        self.statusLabel.setWordWrap(True)

        # what is picked: what it produces, and the green coffee to declare
        self.detail = QFrame()
        self.detail.setVisible(False)
        self.detailTitle = QLabel('')
        self.detailTitle.setObjectName('erpSection')
        self.detailTitle.setWordWrap(True)
        beans = QLabel(QApplication.translate('ERP', 'Cà phê nhân đưa vào mẻ này'))
        beans.setObjectName('erpFieldLabel')
        self.inputsBox = QWidget()
        self.inputsLayout = QVBoxLayout(self.inputsBox)
        self.inputsLayout.setContentsMargins(0, 0, 0, 0)
        self.inputsLayout.setSpacing(6)
        self.totalLabel = QLabel('')
        self.totalLabel.setObjectName('erpSection')
        self.warnLabel = QLabel('')
        self.warnLabel.setObjectName('erpWarn')
        self.warnLabel.setWordWrap(True)
        self.warnLabel.setVisible(False)
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(6)
        detail_layout.addWidget(self.detailTitle)
        detail_layout.addWidget(beans)
        detail_layout.addWidget(self.inputsBox)
        detail_layout.addWidget(self.totalLabel)
        detail_layout.addWidget(self.warnLabel)

        self.errorLabel = QLabel('')
        self.errorLabel.setObjectName('erpError')
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setAccessibleName(QApplication.translate('ERP', 'Lỗi'))
        self.errorLabel.setVisible(False)

        self.start_button = QPushButton(QApplication.translate('ERP', 'Bắt đầu rang'))
        self.start_button.setObjectName('erpPrimary')
        self.start_button.setDefault(True)
        self.start_button.setAutoDefault(True)
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton(QApplication.translate('ERP', 'Huỷ'))
        self.cancel_button.setObjectName('erpSecondary')
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(0)
        layout.addWidget(title)
        layout.addSpacing(2)
        layout.addWidget(subtitle)
        layout.addSpacing(14)
        layout.addWidget(label)
        layout.addSpacing(4)
        layout.addWidget(self.search_edit)
        layout.addSpacing(6)
        layout.addWidget(self.results, 1)
        layout.addWidget(self.statusLabel)
        layout.addSpacing(10)
        layout.addWidget(self.detail)
        layout.addSpacing(8)
        layout.addWidget(self.errorLabel)
        layout.addSpacing(10)
        layout.addWidget(self.start_button)
        layout.addSpacing(4)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setTabOrder(self.search_edit, self.results)
        self.setTabOrder(self.results, self.start_button)
        self.setTabOrder(self.start_button, self.cancel_button)

        self._timer:QTimer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._search_now)
        self.search_edit.textChanged.connect(self._on_text_changed)
        self.search_edit.setFocus()
        QTimer.singleShot(0, self._search_now)

    # ----- background work -------------------------------------------------------------------------------------------------

    def _run(self, kind:str, seq:int, work:Callable[[], Any]) -> None:
        bridge = self._bridge

        def target() -> None:
            result:Any = None
            error:Any = None
            try:
                result = work()
            except Exception as e: # pylint: disable=broad-except
                error = e
            try:
                bridge.done.emit(kind, seq, result, error)
            except RuntimeError:
                pass # the dialog is gone already

        threading.Thread(target=target, name=f'erp-start-{kind}', daemon=True).start()

    # ----- search ----------------------------------------------------------------------------------------------------------

    def _on_text_changed(self, _text:str) -> None:
        self._timer.start(self._debounce_ms)

    @pyqtSlot()
    def _search_now(self) -> None:
        self._seq += 1
        seq = self._seq
        query = self.search_edit.text()
        self.statusLabel.setText(QApplication.translate('ERP', 'Đang tìm…'))
        self._run('search', seq, lambda: self._api.search_commands(query, SEARCH_LIMIT))

    @pyqtSlot(str, int, object, object)
    def _on_done(self, kind:str, seq:int, result:Any, error:Any) -> None:
        if kind == 'search':
            if seq != self._seq:
                return # a newer search is on its way: this answer is stale
            self._on_searched(result, error)
        elif kind == 'start':
            self._on_started(result, error)

    def _on_searched(self, commands:Any, error:Any) -> None:
        if error is not None:
            self.statusLabel.setText('')
            self._show_error(error)
            return
        self.show_error('')
        self._commands = list(commands)
        selected = self.command_selected()
        keep = self._wanted_id or (selected.command_id if selected is not None else None)
        self.results.blockSignals(True)
        self.results.clear()
        for c in self._commands:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, c)
            widget = self._row_widget(c)
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text(c))
            self.results.addItem(item)
            self.results.setItemWidget(item, widget)
            if c.command_id == keep:
                self.results.setCurrentItem(item)
        self.results.blockSignals(False)
        self._wanted_id = None
        if not self._commands:
            self.statusLabel.setText(QApplication.translate('ERP', 'Không có lệnh sản xuất nào khớp. Chỉ hiện lệnh rang còn làm được (chờ hoặc đang thực hiện).'))
        else:
            self.statusLabel.setText(QApplication.translate('ERP', '{0} lệnh. Chọn một lệnh để tiếp tục.').format(len(self._commands)))
        self._on_selected()

    @staticmethod
    def _row_widget(c:RoastCommand) -> QWidget:
        w = QWidget()
        w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        name = QLabel(command_title(c))
        name.setObjectName('erpRowName')
        name.setWordWrap(True)
        sub = QLabel(command_detail(c))
        sub.setObjectName('erpHint')
        sub.setWordWrap(True)
        left = QVBoxLayout()
        left.setSpacing(1)
        left.addWidget(name)
        left.addWidget(sub)
        kind, text = command_badge(c)
        badge = QLabel(text)
        badge.setObjectName('erpBadge')
        badge.setProperty('kind', kind)
        row = QHBoxLayout(w)
        row.setContentsMargins(10, 7, 10, 7)
        row.addLayout(left, 1)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def command_selected(self) -> RoastCommand|None:
        item = self.results.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return data if isinstance(data, RoastCommand) else None

    # ----- the picked command ----------------------------------------------------------------------------------------------

    @pyqtSlot()
    def _on_selected(self) -> None:
        c = self.command_selected()
        previous = {i.inventory_id: spin.value() for i, spin in self._spins}
        self._spins = []
        while self.inputsLayout.count():
            child = self.inputsLayout.takeAt(0)
            widget = child.widget() if child is not None else None
            if widget is not None:
                widget.deleteLater()
        if c is None:
            self.detail.setVisible(False)
            self._update_total()
            return
        self.detail.setVisible(True)
        self.detailTitle.setText(QApplication.translate('ERP', 'Thành phẩm: {0} · {1} kg').format(c.output_name, format_kg(c.target_kg)))
        if not c.inputs:
            note = QLabel(QApplication.translate(
                'ERP', 'Lệnh này chưa có cà phê nhân đã xuất kho (hoặc đã dùng hết). Hãy xuất kho nhân trên ERP trước khi rang.'))
            note.setObjectName('erpWarn')
            note.setWordWrap(True)
            self.inputsLayout.addWidget(note)
        for inp in c.inputs:
            self.inputsLayout.addWidget(self._input_row(inp, previous.get(inp.inventory_id, 0.0)))
        self._update_total()

    def _input_row(self, inp:RoundInput, value:float) -> QWidget:
        w = QWidget()
        name = QLabel(inp.name or inp.code)
        name.setObjectName('erpRowName')
        hint = QLabel(QApplication.translate('ERP', 'Khả dụng: {0} kg').format(format_kg(inp.kg)))
        hint.setObjectName('erpHint')
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(name)
        left.addWidget(hint)
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(0.0, max(inp.kg, 0.0))
        spin.setSingleStep(0.5)
        spin.setSuffix(' kg')
        spin.setValue(min(value, inp.kg))
        spin.setMinimumWidth(150)
        spin.setAccessibleName(QApplication.translate('ERP', 'Số kg {0}').format(inp.name or inp.code))
        spin.valueChanged.connect(lambda *_: self._update_total())
        self._spins.append((inp, spin))
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addLayout(left, 1)
        row.addWidget(spin)
        return w

    def total_kg(self) -> float:
        return round(sum(spin.value() for _, spin in self._spins), 3)

    def _update_total(self) -> None:
        total = self.total_kg()
        self.totalLabel.setText(QApplication.translate('ERP', 'Tổng cà phê nhân: {0} kg').format(format_kg(total)))
        over = self.capacity_kg is not None and self.capacity_kg > 0 and total > self.capacity_kg
        if over and self.capacity_kg is not None:
            self.warnLabel.setText(QApplication.translate('ERP', 'Vượt công suất của {0} ({1} kg). Kiểm tra lại số kg đã nhập.').format(
                self.roaster_name, format_kg(self.capacity_kg)))
        self.warnLabel.setVisible(bool(over))
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.start_button.setEnabled(not self.busy and self.command_selected() is not None and self.total_kg() > 0)

    # ----- start -----------------------------------------------------------------------------------------------------------

    def show_error(self, text:str) -> None:
        self.errorLabel.setText(text)
        self.errorLabel.setVisible(bool(text))

    def _show_error(self, error:Any) -> None:
        if isinstance(error, roasters.SessionExpired):
            self.need_login = True
            self.reject()
        elif isinstance(error, roasters.LeaseLost):
            self.lease_lost = True
            self.show_error(QApplication.translate('ERP', 'Phiên của bạn không còn giữ {0}. Đóng hộp này và chọn lại máy rang.').format(self.roaster_name))
        elif isinstance(error, rounds.RoasterBusy):
            self.busy_with_round = True
            self.show_error(error.message)
        elif isinstance(error, roasters.RoasterForbidden):
            self.show_error(QApplication.translate('ERP', 'Tài khoản này chưa được cấp quyền bắt đầu mẻ rang (production-commands:start).'))
        elif isinstance(error, roasters.RoasterUnavailable):
            self.show_error(QApplication.translate('ERP', 'Không kết nối được tới ERP. Kiểm tra mạng rồi thử lại.')
                            if error.kind == 'network' else QApplication.translate('ERP', 'ERP đang bận hoặc gặp sự cố. Vui lòng thử lại sau ít giây.'))
        elif isinstance(error, roasters.RoasterError):
            self.show_error(error.message)
        else:
            _log.exception(error)
            self.show_error(QApplication.translate('ERP', 'Có lỗi không mong đợi: {0}').format(str(error)))

    def _set_busy(self, busy:bool) -> None:
        self.busy = busy
        self.search_edit.setEnabled(not busy)
        self.results.setEnabled(not busy)
        self.inputsBox.setEnabled(not busy)
        self._update_buttons()

    @pyqtSlot()
    @pyqtSlot(bool)
    def _start(self, _:bool = False) -> None:
        c = self.command_selected()
        lines = [(inp, spin.value()) for inp, spin in self._spins if spin.value() > 0]
        if self.busy or c is None or not lines:
            return
        self.show_error('')
        self._set_busy(True)
        self.start_button.setText(QApplication.translate('ERP', 'Đang bắt đầu…'))
        self._pending = (c, lines)
        self._run('start', 0, lambda: self._api.start_round(
            equipment_id=self._equipment_id, session_id=self._session_id, version=self._version, command_id=c.command_id,
            step_number=c.step_number, inputs=[(inp.inventory_id, kg) for inp, kg in lines]))

    def _on_started(self, started:Any, error:Any) -> None:
        self.start_button.setText(QApplication.translate('ERP', 'Bắt đầu rang'))
        self._set_busy(False)
        if error is not None:
            self._show_error(error)
            if isinstance(error, (rounds.QuotaExceeded, rounds.NotRoastable)):
                self._wanted_id = self._pending[0].command_id if self._pending is not None else None
                self._search_now() # what is left may have changed meanwhile
            return
        if self._pending is not None:
            self.command, self.input_lines = self._pending
        self.started = started
        self.accept()

    @override
    def reject(self) -> None:
        self._timer.stop()
        super().reject()

    @override
    def accept(self) -> None:
        self._timer.stop()
        super().accept()
