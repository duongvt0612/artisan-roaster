#!/usr/bin/python
#
# weigh_dialog.py
#
# ABOUT
# The list of the roast batches of this user that were finished with "Chưa cân được" (the roasted beans were left to cool) and still wait for
# their weight, opened by the "Mẻ chờ cân" button of the banner. The operator picks a batch, types the kg and the ERP takes it at once (it
# answers with the loss, like a normal finish). The dialog stays open so that several batches can be weighed in a row; what is left is read
# by the caller from `remaining` when it closes.

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
from typing import Any, Final, TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QKeySequence, QPalette
from PyQt6.QtWidgets import (QApplication, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget)

from artisanlib.dialogs import ArtisanDialog
from erp import roasters, rounds
from erp.finish_dialog import MAX_OUTPUT_KG, deviation_percent, loss_text
from erp.login import build_stylesheet
from erp.rounds import AwaitingRound, FinishedRound
from erp.start_dialog import format_kg

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)


def row_title(r:AwaitingRound) -> str:
    """'LS-1026-000123 · mẻ #3 · Arabica Cầu Đất rang': which batch of which command, and what it produced."""
    return f'{r.label} · {r.output_name}'


def row_detail(r:AwaitingRound) -> str:
    """The second line of a row: what went in, what the formula expects, when the roast ended and on which roaster."""
    parts = [QApplication.translate('ERP', 'Nhân vào {0} kg · dự kiến {1} kg').format(format_kg(r.input_kg), format_kg(r.expected_output_kg))]
    if r.completed_at is not None:
        parts.append(QApplication.translate('ERP', 'xong lúc {0}').format(r.completed_at.astimezone().strftime('%H:%M %d/%m')))
    if r.roaster_name:
        parts.append(r.roaster_name)
    return ' · '.join(parts)


def row_badge(r:AwaitingRound) -> tuple[str, str]:
    """(kind, text) of the badge of a row. The words carry the meaning, the colour is only an addition."""
    if r.is_test:
        return 'test', QApplication.translate('ERP', 'Mẻ thử · chờ cân')
    return 'waiting', QApplication.translate('ERP', 'Chờ cân')


def accessible_text(r:AwaitingRound) -> str:
    return f'{row_title(r)}, {row_detail(r)}, {row_badge(r)[1]}'


def result_text(label:str, finished:FinishedRound) -> tuple[str, bool]:
    """('LS-… · mẻ #3: 30 kg thành phẩm, hao hụt 25%', exceeded): what the ERP answered, with the alert if the weight is outside what the formula allows."""
    text = QApplication.translate('ERP', 'Đã ghi số cân {0}: {1} kg thành phẩm, hao hụt {2}%').format(
        label, format_kg(finished.output_kg), format_kg(round(finished.loss_percent, 1)))
    if finished.loss_exceeded:
        deviation = deviation_percent(finished.output_kg, finished.expected_output_kg)
        text += QApplication.translate('ERP', ' · vượt ngưỡng cho phép: lệch {0}% so với dự kiến {1} kg (±{2}%). ERP đã báo quản lý.').format(
            ('+' if deviation > 0 else '') + format_kg(round(deviation, 1)), format_kg(finished.expected_output_kg), format_kg(finished.tolerance_percent))
    return text, finished.loss_exceeded


def _css(palette:QPalette) -> str:
    dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
    test_bg, test_fg = ('#4a3510', '#ffd37a') if dark else ('#fdf0d5', '#7a4a00')
    wait_bg, wait_fg = ('#16324f', '#9cc9f5') if dark else ('#e1eefb', '#0f3f73')
    ok_fg = '#9be3b0' if dark else '#14532d'
    bad_bg, bad_fg = ('#3d1f1f', '#ffb4ab') if dark else ('#fdecea', '#9b1c1c')
    edge, btn, btn_hover = ('#5a5a5a', '#2d2d2d', '#3a3a3a') if dark else ('#c4c4c4', '#ffffff', '#f0f0f0')
    return f"""
        QLabel#erpBadge {{ border-radius: 10px; padding: 3px 10px; font-weight: 600; }}
        QLabel#erpBadge[kind="test"] {{ background: {test_bg}; color: {test_fg}; }}
        QLabel#erpBadge[kind="waiting"] {{ background: {wait_bg}; color: {wait_fg}; }}
        QLabel#erpRowName {{ font-size: 14px; font-weight: 600; }}
        QLabel#erpSection {{ font-weight: 600; }}
        QLabel#erpHint {{ font-size: 12px; }}
        QLabel#erpLoss[bad="false"] {{ color: {ok_fg}; font-weight: 600; font-size: 15px; }}
        QLabel#erpLoss[bad="true"] {{ background: {bad_bg}; color: {bad_fg}; font-weight: 600; font-size: 15px; border-radius: 6px; padding: 6px 10px; }}
        QLabel#erpResult[bad="false"] {{ color: {ok_fg}; font-weight: 600; }}
        QLabel#erpResult[bad="true"] {{ background: {bad_bg}; color: {bad_fg}; font-weight: 600; border-radius: 6px; padding: 6px 10px; }}
        QListWidget {{ border-radius: 6px; padding: 2px; outline: 0; }}
        QListWidget::item {{ border-radius: 6px; padding: 2px; }}
        QListWidget::item:selected {{ background: {'#16324f' if dark else '#dbeafe'}; }}
        QPushButton#erpRefresh {{ min-height: 22px; padding: 5px 14px; border: 1px solid {edge}; border-radius: 6px; background: {btn}; }}
        QPushButton#erpRefresh:hover {{ background: {btn_hover}; }}
        QPushButton#erpRefresh:disabled {{ color: {edge}; }}
        QDoubleSpinBox {{ min-height: 26px; padding: 4px 8px; border-radius: 6px; font-size: 16px; }}
    """


class _Bridge(QObject):
    """Delivers the result of a background call to the GUI thread: (kind, sequence number, result, error)."""
    done = pyqtSignal(str, int, object, object)


class WeighPendingDialog(ArtisanDialog):

    __slots__ = [
        'remaining', 'need_login', 'busy', 'weighed', 'results', 'statusLabel', 'resultLabel', 'detail', 'detailTitle', 'detailFacts', 'spin',
        'lossLabel', 'notesEdit', 'errorLabel', 'refresh_button', 'confirm_button', 'close_button', '_api', '_bridge', '_seq', '_rounds',
        '_shown_id', '_notice', '_weighing',
    ]

    def __init__(
        self,
        parent:QWidget|None,
        aw:'ApplicationWindow',
        *,
        api:Any = rounds,
    ) -> None:
        super().__init__(parent, aw)
        # what is left is read after exec() returned
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.remaining:int|None = None            # how many batches still wait for a weight (None: the ERP was not asked successfully yet)
        self.need_login:bool = False
        self.busy:bool = False
        self.weighed:list[FinishedRound] = []     # what the ERP took while this dialog was open
        self._api:Any = api
        self._bridge:_Bridge = _Bridge(self)
        self._bridge.done.connect(self._on_done)
        self._seq:int = 0
        self._rounds:list[AwaitingRound] = []
        self._shown_id:str|None = None            # the batch the weight box belongs to: another batch starts from an empty box
        self._notice:str = ''                     # said after the next list (a batch that was weighed elsewhere)
        self._weighing:AwaitingRound|None = None  # the batch sent to the ERP: its answer belongs to it, whatever is picked meanwhile

        self.setStyleSheet(build_stylesheet(self.palette()) + _css(self.palette()))
        self.setMinimumWidth(640)
        self.setMinimumHeight(600)
        self.setWindowTitle(QApplication.translate('ERP', 'Mẻ chờ cân'))

        title = QLabel(QApplication.translate('ERP', 'Mẻ chờ cân'))
        title.setObjectName('erpTitle')
        subtitle = QLabel(QApplication.translate('ERP', 'Các mẻ của bạn đã rang xong nhưng chưa nhập số kg thành phẩm. Chọn mẻ, cân rồi nhập số kg.'))
        subtitle.setObjectName('erpSubtitle')
        subtitle.setWordWrap(True)

        label = QLabel(QApplication.translate('ERP', 'Mẻ đang chờ cân'))
        label.setObjectName('erpFieldLabel')
        # the list is the ERP's: it is read again on request, without closing the dialog
        self.refresh_button = QPushButton(QApplication.translate('ERP', 'Làm mới'))
        self.refresh_button.setObjectName('erpRefresh')
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.setShortcut(QKeySequence(Qt.Key.Key_F5))
        self.refresh_button.setToolTip(QApplication.translate('ERP', 'Tải lại danh sách mẻ chờ cân từ ERP (F5)'))
        self.refresh_button.setAccessibleName(QApplication.translate('ERP', 'Làm mới danh sách mẻ chờ cân'))
        self.refresh_button.clicked.connect(self.refresh)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(label, 1)
        header.addWidget(self.refresh_button)

        self.results = QListWidget()
        self.results.setAccessibleName(QApplication.translate('ERP', 'Danh sách mẻ chờ cân'))
        self.results.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.setMinimumHeight(190)
        self.results.currentItemChanged.connect(lambda *_: self._on_selected())

        self.statusLabel = QLabel('')
        self.statusLabel.setObjectName('erpHint')
        self.statusLabel.setWordWrap(True)

        self.resultLabel = QLabel('')
        self.resultLabel.setObjectName('erpResult')
        self.resultLabel.setProperty('bad', False)
        self.resultLabel.setWordWrap(True)
        self.resultLabel.setAccessibleName(QApplication.translate('ERP', 'Kết quả lần cân vừa rồi'))
        self.resultLabel.setVisible(False)

        # what is picked: what the batch is, and the weight to type
        self.detail = QFrame()
        self.detail.setVisible(False)
        self.detailTitle = QLabel('')
        self.detailTitle.setObjectName('erpSection')
        self.detailTitle.setWordWrap(True)
        self.detailFacts = QLabel('')
        self.detailFacts.setObjectName('erpHint')
        self.detailFacts.setWordWrap(True)
        weight_label = QLabel(QApplication.translate('ERP', 'Số kg bán thành phẩm thực tế (cân sau khi rang xong)'))
        weight_label.setObjectName('erpFieldLabel')
        weight_label.setWordWrap(True)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(3)
        self.spin.setRange(0.0, MAX_OUTPUT_KG)
        self.spin.setSingleStep(0.1)
        self.spin.setSuffix(' kg')
        self.spin.setValue(0.0)
        self.spin.setAccessibleName(QApplication.translate('ERP', 'Số kg thành phẩm'))
        self.spin.valueChanged.connect(lambda *_: self._update_loss())
        weight_label.setBuddy(self.spin)
        self.lossLabel = QLabel('')
        self.lossLabel.setObjectName('erpLoss')
        self.lossLabel.setProperty('bad', False)
        self.lossLabel.setWordWrap(True)
        notes_label = QLabel(QApplication.translate('ERP', 'Ghi chú (không bắt buộc)'))
        notes_label.setObjectName('erpFieldLabel')
        self.notesEdit = QLineEdit()
        self.notesEdit.setMaxLength(500)
        notes_label.setBuddy(self.notesEdit)
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(4)
        detail_layout.addWidget(self.detailTitle)
        detail_layout.addWidget(self.detailFacts)
        detail_layout.addSpacing(6)
        detail_layout.addWidget(weight_label)
        detail_layout.addWidget(self.spin)
        detail_layout.addWidget(self.lossLabel)
        detail_layout.addSpacing(4)
        detail_layout.addWidget(notes_label)
        detail_layout.addWidget(self.notesEdit)

        self.errorLabel = QLabel('')
        self.errorLabel.setObjectName('erpError')
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setAccessibleName(QApplication.translate('ERP', 'Lỗi'))
        self.errorLabel.setVisible(False)

        self.confirm_button = QPushButton(QApplication.translate('ERP', 'Xác nhận số cân'))
        self.confirm_button.setObjectName('erpPrimary')
        self.confirm_button.setDefault(True)
        self.confirm_button.setAutoDefault(True)
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self._confirm)
        self.close_button = QPushButton(QApplication.translate('ERP', 'Đóng'))
        self.close_button.setObjectName('erpSecondary')
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(0)
        layout.addWidget(title)
        layout.addSpacing(2)
        layout.addWidget(subtitle)
        layout.addSpacing(14)
        layout.addLayout(header)
        layout.addSpacing(6)
        layout.addWidget(self.results, 1)
        layout.addWidget(self.statusLabel)
        layout.addSpacing(6)
        layout.addWidget(self.resultLabel)
        layout.addSpacing(10)
        layout.addWidget(self.detail)
        layout.addSpacing(8)
        layout.addWidget(self.errorLabel)
        layout.addSpacing(10)
        layout.addWidget(self.confirm_button)
        layout.addSpacing(4)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setTabOrder(self.refresh_button, self.results)
        self.setTabOrder(self.results, self.spin)
        self.setTabOrder(self.spin, self.notesEdit)
        self.setTabOrder(self.notesEdit, self.confirm_button)
        self.setTabOrder(self.confirm_button, self.close_button)
        self.results.setFocus()
        QTimer.singleShot(0, self.refresh)

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

        threading.Thread(target=target, name=f'erp-weigh-{kind}', daemon=True).start()

    @pyqtSlot()
    @pyqtSlot(bool)
    def refresh(self, _:bool = False) -> None:
        self._seq += 1
        seq = self._seq
        self.statusLabel.setText(QApplication.translate('ERP', 'Đang tải danh sách…'))
        self._run('list', seq, self._api.list_awaiting_weigh)

    @pyqtSlot(str, int, object, object)
    def _on_done(self, kind:str, seq:int, result:Any, error:Any) -> None:
        if kind == 'list':
            if seq != self._seq:
                return # a newer list is on its way: this answer is stale
            self._on_listed(result, error)
        elif kind == 'weigh':
            self._on_weighed(result, error)

    # ----- the list --------------------------------------------------------------------------------------------------------

    def _on_listed(self, listed:Any, error:Any) -> None:
        if error is not None:
            self.statusLabel.setText('')
            self._show_error(error, listing=True)
            return
        self.show_error('')
        self._rounds = list(listed)
        self.remaining = len(self._rounds)
        self._fill_list(keep=self._shown_id)
        notice, self._notice = self._notice, ''
        self.statusLabel.setText((notice + ' ' if notice else '') + self._count_text())
        self._on_selected()

    def _count_text(self) -> str:
        if not self._rounds:
            return QApplication.translate('ERP', 'Không có mẻ nào chờ cân.')
        return QApplication.translate('ERP', '{0} mẻ chờ cân. Chọn một mẻ rồi nhập số kg đã cân.').format(len(self._rounds))

    def _fill_list(self, keep:str|None) -> None:
        self.results.blockSignals(True)
        self.results.clear()
        chosen:QListWidgetItem|None = None
        for r in self._rounds:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r)
            widget = self._row_widget(r)
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text(r))
            self.results.addItem(item)
            self.results.setItemWidget(item, widget)
            if keep is not None and r.round_id == keep:
                chosen = item
        if chosen is None and self.results.count() > 0:
            chosen = self.results.item(0) # nothing was shown before: the oldest, they are weighed in the order they were roasted
        if chosen is not None:
            self.results.setCurrentItem(chosen)
        self.results.blockSignals(False)

    @staticmethod
    def _row_widget(r:AwaitingRound) -> QWidget:
        w = QWidget()
        w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        name = QLabel(row_title(r))
        name.setObjectName('erpRowName')
        name.setWordWrap(True)
        sub = QLabel(row_detail(r))
        sub.setObjectName('erpHint')
        sub.setWordWrap(True)
        left = QVBoxLayout()
        left.setSpacing(1)
        left.addWidget(name)
        left.addWidget(sub)
        kind, text = row_badge(r)
        badge = QLabel(text)
        badge.setObjectName('erpBadge')
        badge.setProperty('kind', kind)
        row = QHBoxLayout(w)
        row.setContentsMargins(10, 7, 10, 7)
        row.addLayout(left, 1)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def round_selected(self) -> AwaitingRound|None:
        item = self.results.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return data if isinstance(data, AwaitingRound) else None

    # ----- the picked batch ------------------------------------------------------------------------------------------------

    @pyqtSlot()
    def _on_selected(self) -> None:
        r = self.round_selected()
        if r is None:
            self._shown_id = None
            self.detail.setVisible(False)
            self._update_buttons()
            return
        self.detail.setVisible(True)
        self.detailTitle.setText(row_title(r))
        self.detailFacts.setText(row_detail(r))
        if r.round_id != self._shown_id:
            self._shown_id = r.round_id
            self.spin.setValue(0.0) # another batch: never carry a weight over
            self.notesEdit.clear()
            self.spin.setFocus()
            self.spin.selectAll()
        self._update_loss()

    def _update_loss(self) -> None:
        r = self.round_selected()
        text, bad = loss_text(r.input_kg, self.spin.value(), r.expected_output_kg, r.tolerance_percent) if r is not None else ('', False)
        self.lossLabel.setText(text)
        self.lossLabel.setVisible(bool(text))
        self._restyle(self.lossLabel, bad)
        self._update_buttons()

    @staticmethod
    def _restyle(label:QLabel, bad:bool) -> None:
        label.setProperty('bad', bad)
        style = label.style()
        if style is not None:
            style.unpolish(label)
            style.polish(label)

    def _update_buttons(self) -> None:
        self.confirm_button.setEnabled(not self.busy and self.round_selected() is not None and self.spin.value() > 0)
        self.refresh_button.setEnabled(not self.busy)
        self.close_button.setEnabled(not self.busy)

    def reject(self) -> None:
        if self.busy:
            return # the ERP is taking a weight: closing now would lose its answer
        super().reject()

    def _set_busy(self, busy:bool) -> None:
        self.busy = busy
        self.results.setEnabled(not busy)
        self.spin.setEnabled(not busy)
        self.notesEdit.setEnabled(not busy)
        self._update_buttons()

    # ----- weighing --------------------------------------------------------------------------------------------------------

    def show_error(self, text:str) -> None:
        self.errorLabel.setText(text)
        self.errorLabel.setVisible(bool(text))

    def _show_error(self, error:Any, *, listing:bool = False) -> None:
        if isinstance(error, roasters.SessionExpired):
            self.need_login = True
            self.reject()
        elif isinstance(error, roasters.RoasterForbidden):
            self.show_error(error.message or QApplication.translate('ERP', 'Bạn không có quyền nhập số cân cho mẻ này.'))
        elif isinstance(error, roasters.RoasterNotFound) and listing:
            self.show_error(QApplication.translate('ERP', 'ERP này chưa hỗ trợ cân sau trên Artisan. Hãy nhập số cân trên web ERP.'))
        elif isinstance(error, roasters.RoasterUnavailable):
            self.show_error(QApplication.translate('ERP', 'Không kết nối được tới ERP. Kiểm tra mạng rồi thử lại: số kg bạn đã nhập vẫn được giữ.')
                            if error.kind == 'network' else QApplication.translate('ERP', 'ERP đang bận hoặc gặp sự cố. Vui lòng thử lại sau ít giây.'))
        elif isinstance(error, roasters.RoasterError):
            self.show_error(error.message)
        else:
            _log.exception(error)
            self.show_error(QApplication.translate('ERP', 'Có lỗi không mong đợi: {0}').format(str(error)))

    @pyqtSlot()
    @pyqtSlot(bool)
    def _confirm(self, _:bool = False) -> None:
        r = self.round_selected()
        kg = self.spin.value()
        if self.busy or r is None or kg <= 0:
            return
        self.show_error('')
        self._weighing = r
        self._set_busy(True)
        self.confirm_button.setText(QApplication.translate('ERP', 'Đang gửi lên ERP…'))
        notes = self.notesEdit.text().strip()
        api = self._api
        self._run('weigh', 0, lambda: api.weigh_round(r.round_id, output_kg=kg, notes=notes or None))

    def _on_weighed(self, finished:Any, error:Any) -> None:
        r, self._weighing = self._weighing, None
        self.confirm_button.setText(QApplication.translate('ERP', 'Xác nhận số cân'))
        self._set_busy(False)
        if error is not None:
            if isinstance(error, rounds.RoundNotAwaiting) and r is not None:
                # weighed on the web meanwhile (or cancelled): the row has no business being here
                self._notice = QApplication.translate('ERP', 'Mẻ {0} đã được cân ở nơi khác hoặc đã bị huỷ.').format(r.label)
                self.refresh()
                return
            self._show_error(error)
            return
        self.weighed.append(finished)
        self._seq += 1 # a list asked before the weight was taken still has this batch in it: its answer is stale
        if r is not None:
            self._rounds = [x for x in self._rounds if x.round_id != r.round_id]
            text, bad = result_text(r.label, finished)
            self.resultLabel.setText(text)
            self._restyle(self.resultLabel, bad)
            self.resultLabel.setVisible(True)
        self.remaining = len(self._rounds)
        self._shown_id = None
        self._fill_list(keep=None)
        self.statusLabel.setText(self._count_text())
        self._on_selected()
