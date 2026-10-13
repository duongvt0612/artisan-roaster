#!/usr/bin/python
#
# finish_dialog.py
#
# ABOUT
# The dialog that ends a roast batch on the ERP: the operator weighs the roasted beans and types the kg. It shows the kg that went in,
# the kg the formula expects and the loss ("hao hụt") of what he typed, red when it exceeds what the formula allows, so that a
# mistyped weight is noticed before the ERP raises its alert. Only when the ERP has accepted the weight does the dialog close.

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
from typing import Any, Final, TYPE_CHECKING, override

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QDoubleSpinBox, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from artisanlib.dialogs import ArtisanDialog
from erp import roasters, rounds
from erp.batch import Batch, loss_percent
from erp.login import build_stylesheet
from erp.rounds import FinishedRound
from erp.start_dialog import format_kg

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)

MAX_OUTPUT_KG: Final[float] = 100000.0


def loss_text(input_kg:float, output_kg:float, tolerance_percent:float) -> tuple[str, bool]:
    """('Hao hụt: 18,5% (tối đa 15%)', exceeded). A weight above what went in is not a loss: it is flagged as a doubtful weighing."""
    if output_kg <= 0:
        return '', False
    if output_kg > input_kg:
        return QApplication.translate('ERP', 'Số kg thành phẩm lớn hơn cà phê nhân đã đưa vào: hãy kiểm tra lại cân.'), True
    loss = loss_percent(input_kg, output_kg)
    text = QApplication.translate('ERP', 'Hao hụt: {0}%').format(format_kg(round(loss, 1)))
    if tolerance_percent > 0:
        text += QApplication.translate('ERP', ' (tối đa {0}%)').format(format_kg(tolerance_percent))
    return text, tolerance_percent > 0 and loss > tolerance_percent


def _css(palette:QPalette) -> str:
    dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
    ok_fg = '#9be3b0' if dark else '#14532d'
    bad_bg, bad_fg = ('#3d1f1f', '#ffb4ab') if dark else ('#fdecea', '#9b1c1c')
    return f"""
        QLabel#erpSection {{ font-weight: 600; }}
        QLabel#erpFact {{ font-size: 14px; }}
        QLabel#erpLoss[bad="false"] {{ color: {ok_fg}; font-weight: 600; font-size: 15px; }}
        QLabel#erpLoss[bad="true"] {{ background: {bad_bg}; color: {bad_fg}; font-weight: 600; font-size: 15px; border-radius: 6px; padding: 6px 10px; }}
        QDoubleSpinBox {{ min-height: 26px; padding: 4px 8px; border-radius: 6px; font-size: 16px; }}
    """


class _Bridge(QObject):
    done = pyqtSignal(object, object)


class FinishRoundDialog(ArtisanDialog):

    __slots__ = [
        'batch', 'result_round', 'already_over', 'need_login', 'busy', 'spin', 'lossLabel', 'colorEdit', 'notesEdit', 'errorLabel',
        'finish_button', 'later_button', '_api', '_bridge',
    ]

    def __init__(
        self,
        parent:QWidget|None,
        aw:'ApplicationWindow',
        *,
        batch:Batch,
        powder_color:str = '',
        api:Any = rounds,
    ) -> None:
        super().__init__(parent, aw)
        # the results are read after exec() returned
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.batch:Batch = batch
        self.result_round:FinishedRound|None = None   # set once the ERP accepted the weight (not `finished`: that is a signal of QDialog)
        self.already_over:bool = False            # the ERP says the batch is not running any more (finished or cancelled elsewhere)
        self.need_login:bool = False
        self.busy:bool = False
        self._api:Any = api
        self._bridge:_Bridge = _Bridge(self)
        self._bridge.done.connect(self._on_done)

        self.setStyleSheet(build_stylesheet(self.palette()) + _css(self.palette()))
        self.setMinimumWidth(520)
        self.setWindowTitle(QApplication.translate('ERP', 'Kết thúc mẻ rang'))

        title = QLabel(QApplication.translate('ERP', 'Kết thúc mẻ rang'))
        title.setObjectName('erpTitle')
        subtitle = QLabel(batch.label + (QApplication.translate('ERP', ' · Mẻ thử') if batch.is_test else ''))
        subtitle.setObjectName('erpSubtitle')
        facts = QLabel(QApplication.translate('ERP', 'Thành phẩm: {0}\nCà phê nhân đã đưa vào: {1} kg\nDự kiến thu được theo công thức: {2} kg').format(
            batch.output_name, format_kg(batch.input_kg), format_kg(batch.expected_output_kg)))
        facts.setObjectName('erpFact')
        facts.setWordWrap(True)

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
        self.spin.valueChanged.connect(lambda *_: self._update())
        weight_label.setBuddy(self.spin)

        self.lossLabel = QLabel('')
        self.lossLabel.setObjectName('erpLoss')
        self.lossLabel.setProperty('bad', False)
        self.lossLabel.setWordWrap(True)

        color_label = QLabel(QApplication.translate('ERP', 'Màu bột (không bắt buộc)'))
        color_label.setObjectName('erpFieldLabel')
        self.colorEdit = QLineEdit(powder_color)
        self.colorEdit.setPlaceholderText(QApplication.translate('ERP', 'Ví dụ: #85'))
        self.colorEdit.setMaxLength(50)
        color_label.setBuddy(self.colorEdit)
        notes_label = QLabel(QApplication.translate('ERP', 'Ghi chú (không bắt buộc)'))
        notes_label.setObjectName('erpFieldLabel')
        self.notesEdit = QLineEdit()
        self.notesEdit.setMaxLength(500)
        notes_label.setBuddy(self.notesEdit)

        self.errorLabel = QLabel('')
        self.errorLabel.setObjectName('erpError')
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setAccessibleName(QApplication.translate('ERP', 'Lỗi'))
        self.errorLabel.setVisible(False)

        self.finish_button = QPushButton(QApplication.translate('ERP', 'Hoàn thành mẻ rang'))
        self.finish_button.setObjectName('erpPrimary')
        self.finish_button.setDefault(True)
        self.finish_button.setAutoDefault(True)
        self.finish_button.setEnabled(False)
        self.finish_button.clicked.connect(self._finish)
        self.later_button = QPushButton(QApplication.translate('ERP', 'Để sau'))
        self.later_button.setObjectName('erpSecondary')
        self.later_button.setAutoDefault(False)
        self.later_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(0)
        layout.addWidget(title)
        layout.addSpacing(2)
        layout.addWidget(subtitle)
        layout.addSpacing(12)
        layout.addWidget(facts)
        layout.addSpacing(14)
        layout.addWidget(weight_label)
        layout.addSpacing(4)
        layout.addWidget(self.spin)
        layout.addSpacing(6)
        layout.addWidget(self.lossLabel)
        layout.addSpacing(10)
        layout.addWidget(color_label)
        layout.addSpacing(4)
        layout.addWidget(self.colorEdit)
        layout.addSpacing(8)
        layout.addWidget(notes_label)
        layout.addSpacing(4)
        layout.addWidget(self.notesEdit)
        layout.addSpacing(8)
        layout.addWidget(self.errorLabel)
        layout.addSpacing(12)
        layout.addWidget(self.finish_button)
        layout.addSpacing(4)
        layout.addWidget(self.later_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setTabOrder(self.spin, self.colorEdit)
        self.setTabOrder(self.colorEdit, self.notesEdit)
        self.setTabOrder(self.notesEdit, self.finish_button)
        self.setTabOrder(self.finish_button, self.later_button)
        self.spin.setFocus()
        self.spin.selectAll()
        self._update()

    def _update(self) -> None:
        text, bad = loss_text(self.batch.input_kg, self.spin.value(), self.batch.tolerance_percent)
        self.lossLabel.setText(text)
        self.lossLabel.setVisible(bool(text))
        self.lossLabel.setProperty('bad', bad)
        style = self.lossLabel.style()
        if style is not None:
            style.unpolish(self.lossLabel)
            style.polish(self.lossLabel)
        self.finish_button.setEnabled(not self.busy and self.spin.value() > 0)

    def show_error(self, text:str) -> None:
        self.errorLabel.setText(text)
        self.errorLabel.setVisible(bool(text))

    @pyqtSlot()
    @pyqtSlot(bool)
    def _finish(self, _:bool = False) -> None:
        kg = self.spin.value()
        if self.busy or kg <= 0:
            return
        self.show_error('')
        self.busy = True
        self.spin.setEnabled(False)
        self.finish_button.setEnabled(False)
        self.finish_button.setText(QApplication.translate('ERP', 'Đang gửi lên ERP…'))
        color, notes = self.colorEdit.text().strip(), self.notesEdit.text().strip()
        bridge, api, round_id = self._bridge, self._api, self.batch.round_id

        def target() -> None:
            result:Any = None
            error:Any = None
            try:
                result = api.finish_round(round_id, output_kg=kg, notes=notes or None, powder_color=color or None)
            except Exception as e: # pylint: disable=broad-except
                error = e
            try:
                bridge.done.emit(result, error)
            except RuntimeError:
                pass # the dialog is gone already

        threading.Thread(target=target, name='erp-finish-round', daemon=True).start()

    @pyqtSlot(object, object)
    def _on_done(self, result:Any, error:Any) -> None:
        self.busy = False
        self.spin.setEnabled(True)
        self.finish_button.setText(QApplication.translate('ERP', 'Hoàn thành mẻ rang'))
        self._update()
        if error is None:
            self.result_round = result
            self.accept()
            return
        if isinstance(error, roasters.SessionExpired):
            self.need_login = True
            self.reject()
        elif isinstance(error, rounds.RoundNotRunning):
            self.already_over = True
            self.accept()
        elif isinstance(error, roasters.RoasterForbidden):
            self.show_error(error.message or QApplication.translate('ERP', 'Bạn không có quyền kết thúc mẻ này.'))
        elif isinstance(error, roasters.RoasterUnavailable):
            self.show_error(QApplication.translate('ERP', 'Không kết nối được tới ERP. Kiểm tra mạng rồi thử lại: số kg bạn đã nhập vẫn được giữ.')
                            if error.kind == 'network' else QApplication.translate('ERP', 'ERP đang bận hoặc gặp sự cố. Vui lòng thử lại sau ít giây.'))
        elif isinstance(error, roasters.RoasterError):
            self.show_error(error.message)
        else:
            _log.exception(error)
            self.show_error(QApplication.translate('ERP', 'Có lỗi không mong đợi: {0}').format(str(error)))

    @override
    def reject(self) -> None:
        super().reject()
