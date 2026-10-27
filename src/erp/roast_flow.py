#!/usr/bin/python
#
# roast_flow.py
#
# ABOUT
# What happens around a roast batch ("mẻ rang") between pressing START and the weight of the roasted beans reaching the ERP:
#   * START asks for the production command and creates the batch on the ERP (before_start); Artisan only records after that.
#   * Once the roast is stopped (or when the operator presses the button of the banner after DROP) the batch is finished (finish_batch).
#   * RESET with a batch running asks whether to cancel it on the ERP (before_reset).
#   * After a crash the ERP still has the batch: Artisan asks what to do with it when it is started again (resume_if_any).
# The ERP is the source of truth, nothing is stored locally. The module talks to Artisan only through the few attributes of
# `aw.qmc` it needs (weight, title, beans, flagstart, timeindex, roastepoch, ground_color).

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
from collections.abc import Callable
from typing import Any, Final, TYPE_CHECKING, cast

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from erp import background, batch as erp_batch, finish_dialog, roast_files, roasters, rounds, start_dialog
from erp.attachments import UploadQueue, Uploader
from erp.batch import Batch
from erp.lease import LeaseManager
from erp.rounds import ActiveRound

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)

STOP_PROMPT_DELAY_MS: Final[int] = 300 # let Artisan finish stopping before the dialog opens


class _Flow:
    """What this run of Artisan knows about the batch. One per process, see _flow."""

    def __init__(self) -> None:
        self.batch:Batch|None = None             # started here (or taken over after a crash) and not finished yet
        self.foreign:ActiveRound|None = None     # a batch of somebody else is running on this roaster
        self.linked:Batch|None = None            # the batch of the recording shown now (it stays after the batch is finished, until RESET)
        self.linked_epoch:int|None = None        # `qmc.roastepoch` of that recording: ties the profile to the batch, not to any other profile
        self.on_change:Callable[[], None]|None = None
        self.files_wait:Batch|None = None        # finished while the roast was still recording: its files are built once recording stops
        self.queue:UploadQueue|None = None       # where the files of finished batches wait for the ERP (None: files are not sent)
        self.uploader:Uploader|None = None


_flow:_Flow = _Flow()


def _reset_state() -> None:
    """For tests."""
    global _flow # pylint: disable=global-statement
    _flow = _Flow()


def set_uploads(queue:UploadQueue|None, uploader:Uploader|None) -> None:
    """The queue and the sender of the files of finished batches (None, None switches it off)."""
    _flow.queue = queue
    _flow.uploader = uploader


def set_listener(listener:Callable[[], None]|None) -> None:
    """listener() is called whenever the batch changes: the banner and the window title show it."""
    _flow.on_change = listener


def _changed() -> None:
    listener = _flow.on_change
    if listener is not None:
        try:
            listener()
        except Exception as e: # pylint: disable=broad-except
            _log.exception(e)


def current_batch() -> Batch|None:
    return _flow.batch


def has_unfinished_batch() -> bool:
    return _flow.batch is not None


def title_suffix() -> str:
    b = _flow.batch
    if b is None:
        return ''
    return QApplication.translate('ERP', ' · Đang rang {0} ({1} kg)').format(b.label, start_dialog.format_kg(b.input_kg))


def banner_text() -> str:
    """The part of the banner that says which batch is running ('' if none)."""
    b = _flow.batch
    if b is None:
        return ''
    return QApplication.translate('ERP', 'Đang rang {0} · {1} · {2} kg').format(b.label, b.output_name, start_dialog.format_kg(b.input_kg))


# ---- messages (replaced in tests) -----------------------------------------------------------------------------------------

def _info(aw:'ApplicationWindow', text:str, detail:str = '') -> None:
    box = QMessageBox(aw)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle('ERP')
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    box.exec()


def _choose(aw:'ApplicationWindow', text:str, detail:str, labels:list[str], default:int = 0) -> int:
    """A question with one button per label; returns the index of the clicked one, -1 if the box was closed otherwise."""
    box = QMessageBox(aw)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle('ERP')
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    buttons = [box.addButton(label, QMessageBox.ButtonRole.AcceptRole if i == 0 else QMessageBox.ButtonRole.ActionRole) for i, label in enumerate(labels)]
    box.setDefaultButton(buttons[default])
    box.exec()
    clicked = box.clickedButton()
    return buttons.index(clicked) if clicked in buttons else -1


def _ask_reason(aw:'ApplicationWindow') -> str|None:
    """The reason of a cancellation (required: it ends up in the ERP's log). None if the operator gave up."""
    while True:
        text, ok = QInputDialog.getText(aw, QApplication.translate('ERP', 'Huỷ mẻ rang'),
                                        QApplication.translate('ERP', 'Lý do huỷ mẻ (bắt buộc, ví dụ: rang hỏng, nhầm lệnh):'))
        if not ok:
            return None
        if text.strip():
            return text.strip()


# ---- Artisan's own fields ---------------------------------------------------------------------------------------------------

def _set_weight(aw:'ApplicationWindow', *, input_kg:float|None = None, output_kg:float|None = None) -> None:
    """Writes the weights into Artisan's roast properties, in the unit Artisan uses."""
    from artisanlib.util import convertWeight, weight_units # pylint: disable=import-outside-toplevel
    weight_in, weight_out, unit = aw.qmc.weight
    idx = weight_units.index(unit) if unit in weight_units else 1
    if input_kg is not None:
        weight_in = convertWeight(input_kg, 1, idx)
    if output_kg is not None:
        weight_out = convertWeight(output_kg, 1, idx)
    aw.qmc.weight = (weight_in, weight_out, unit)


def _apply_started(aw:'ApplicationWindow', command:rounds.RoastCommand, lines:list[tuple[rounds.RoundInput, float]], input_kg:float) -> None:
    try:
        _set_weight(aw, input_kg=input_kg, output_kg=0.0)
        aw.qmc.title = command.output_name
        aw.qmc.beans = ', '.join(inp.name or inp.code for inp, _kg in lines)
    except Exception as e: # pylint: disable=broad-except
        _log.exception(e) # cosmetic: the batch exists on the ERP either way


def _powder_color(aw:'ApplicationWindow') -> str:
    try:
        value = float(aw.qmc.ground_color)
    except (AttributeError, TypeError, ValueError):
        return ''
    return f'#{int(value)}' if value > 0 else ''


def _recording(aw:'ApplicationWindow') -> bool:
    return bool(getattr(aw.qmc, 'flagstart', False))


def _dropped(aw:'ApplicationWindow') -> bool:
    try:
        return aw.qmc.timeindex[6] != 0
    except (AttributeError, IndexError, TypeError):
        return False


# ---- START ------------------------------------------------------------------------------------------------------------------

def before_start(aw:'ApplicationWindow', lease:LeaseManager|None, *, capacity_kg:float|None, relogin:Callable[[], bool]) -> bool:
    """Called when START is pressed, before anything is recorded. True: the batch exists on the ERP, Artisan may record."""
    if _flow.batch is not None:
        # the previous batch was never finished: it has to be, or Artisan loses the weight of those beans
        _info(aw, QApplication.translate('ERP', 'Mẻ {0} chưa được kết thúc trên ERP.').format(_flow.batch.label),
              QApplication.translate('ERP', 'Hãy nhập số kg thành phẩm của mẻ đó trước khi bắt đầu mẻ mới.'))
        if not finish_batch(aw, force=True):
            return False
    if lease is None or not lease.is_ok:
        _info(aw, QApplication.translate('ERP', 'Bạn chưa giữ máy rang nào.'),
              QApplication.translate('ERP', 'Hãy chọn lại máy rang (nút “Chọn lại máy” trên dải thông báo) rồi bấm START.'))
        return False
    if _flow.foreign is not None and not _recheck_foreign(aw, lease):
        return False

    dialog = start_dialog.StartRoundDialog(aw, aw, roaster_name=lease.roaster_name, equipment_id=lease.roaster_id, version=lease.version,
                                           capacity_kg=capacity_kg)
    try:
        accepted = bool(dialog.exec())
        started, command, lines = dialog.started, dialog.command, dialog.input_lines
        need_login, busy = dialog.need_login, dialog.busy_with_round
    finally:
        dialog.deleteLater()
    if need_login:
        relogin()
        return False
    if busy:
        _handle_running_batch(aw, lease)
        return False
    if not accepted or started is None or command is None:
        return False
    input_kg = round(sum(kg for _inp, kg in lines), 4)
    _flow.batch = erp_batch.from_started(started, command, input_kg, lease.roaster_id)
    _flow.foreign = None
    _flow.linked = None
    _flow.linked_epoch = None
    _apply_started(aw, command, lines, input_kg)
    _changed()
    return True


def recording_started(aw:'ApplicationWindow') -> None:
    """Called right after Artisan began recording: remembers which recording belongs to the batch."""
    if _flow.batch is not None:
        _flow.linked = _flow.batch
        _flow.linked_epoch = int(getattr(aw.qmc, 'roastepoch', 0) or 0) or None


def _ask_active_round(aw:'ApplicationWindow', roaster_id:str) -> ActiveRound|None:
    """The batch running on the roaster, asked of the ERP while the event loop keeps running (the operator can give up waiting: Cancelled)."""
    return cast('ActiveRound|None', background.call_with_wait(
        aw, QApplication.translate('ERP', 'Đang kiểm tra máy rang với ERP…'), lambda: rounds.active_round(roaster_id), cancellable=True))


def _recheck_foreign(aw:'ApplicationWindow', lease:LeaseManager) -> bool:
    """Somebody else's batch blocked the roaster earlier: asks the ERP whether it still does. True if START may go on."""
    try:
        active = _ask_active_round(aw, lease.roaster_id)
    except background.Cancelled:
        return False # he did not want to wait: START asks again next time
    except roasters.RoasterError as e:
        _info(aw, QApplication.translate('ERP', 'Không kiểm tra được máy rang với ERP.'), e.message)
        return False
    if active is None:
        _flow.foreign = None
        _changed()
        return True
    return _handle_active(aw, active, lease)


def _handle_running_batch(aw:'ApplicationWindow', lease:LeaseManager) -> None:
    """START was refused because the roaster has a batch (ROASTER_BUSY): finds out whose and what to do."""
    try:
        active = _ask_active_round(aw, lease.roaster_id)
    except background.Cancelled:
        return
    except roasters.RoasterError as e:
        _info(aw, QApplication.translate('ERP', 'Máy rang đang có một mẻ chưa kết thúc.'), e.message)
        return
    if active is not None:
        _handle_active(aw, active, lease)


def _describe(active:ActiveRound) -> str:
    started = active.started_at.astimezone().strftime('%H:%M %d/%m') if active.started_at is not None else ''
    who = active.started_by_name or QApplication.translate('ERP', 'người khác')
    return QApplication.translate('ERP', '{0} · mẻ #{1} · {2} kg · bắt đầu {3} bởi {4}').format(
        active.command_code, active.round_number, start_dialog.format_kg(active.input_kg), started, who)


def _handle_active(aw:'ApplicationWindow', active:ActiveRound, lease:LeaseManager) -> bool:
    """A batch is running on the roaster. Returns True if START may go on (never: either it is dealt with first or it blocks)."""
    if not active.mine:
        _flow.foreign = active
        _changed()
        _info(aw, QApplication.translate('ERP', 'Máy rang đang có một mẻ của người khác chưa kết thúc.'),
              QApplication.translate('ERP', '{0}\n\nBạn chưa thể bắt đầu mẻ mới trên máy này cho tới khi mẻ đó được kết thúc hoặc huỷ trên ERP.').format(_describe(active)))
        return False
    _offer_resume(aw, active, lease)
    return False


def _offer_resume(aw:'ApplicationWindow', active:ActiveRound, lease:LeaseManager) -> None:
    choice = _choose(
        aw, QApplication.translate('ERP', 'Máy rang đang có một mẻ của bạn chưa kết thúc.'),
        QApplication.translate('ERP', '{0}\n\nNếu Artisan đã đóng đột ngột, đường rang chưa lưu của mẻ này có thể đã mất. Bạn vẫn có thể kết thúc mẻ trên ERP bằng số kg thành phẩm đã cân.').format(_describe(active)),
        [QApplication.translate('ERP', 'Kết thúc mẻ ngay'), QApplication.translate('ERP', 'Để sau'), QApplication.translate('ERP', 'Huỷ mẻ này')])
    adopted = erp_batch.from_active(active, lease.roaster_id)
    if choice == 2:
        reason = _ask_reason(aw)
        if reason is not None and _cancel_on_erp(aw, adopted.round_id, reason):
            _flow.batch = None
            _changed()
        else:
            _flow.batch = adopted
            _changed()
        return
    _flow.batch = adopted
    _flow.foreign = None
    _changed()
    if choice == 0:
        finish_batch(aw, force=True)


def resume_if_any(aw:'ApplicationWindow', lease:LeaseManager|None) -> None:
    """Right after the roaster was chosen: a batch left running by a crash (or by the ERP web) is offered for finishing or cancelling."""
    if lease is None:
        return
    roaster_id = lease.roaster_id

    def answered(active:Any, error:BaseException|None) -> None:
        if error is not None:
            if isinstance(error, roasters.RoasterError):
                _log.info('could not ask the ERP for the running batch: %s', error.message) # START asks again, the ERP refuses a second batch anyway
            else:
                _log.error('asking the ERP for the running batch failed: %r', error)
            return
        if _flow.batch is not None or not lease.is_ok:
            return # the operator did not wait for the answer (he started a batch, or the roaster is not ours any more): it is stale
        if active is None:
            _flow.foreign = None
            return
        _handle_active(aw, active, lease)

    # not awaited: the main window is usable while the ERP answers, the question is asked when the answer arrives
    background.run_in_thread(lambda: rounds.active_round(roaster_id), answered, name='erp-active-round')


# ---- finish / cancel --------------------------------------------------------------------------------------------------------

def finish_batch(aw:'ApplicationWindow', *, force:bool = False) -> bool:
    """Opens the dialog that ends the batch on the ERP. True if the batch is over now (finished, or it was over already).

    Before DROP the roast is still going on: without `force` the operator is told to wait, so that nobody ends a batch by accident."""
    b = _flow.batch
    if b is None:
        return True
    if not force and _recording(aw) and not _dropped(aw):
        _info(aw, QApplication.translate('ERP', 'Mẻ rang chưa DROP.'), QApplication.translate('ERP', 'Hãy kết thúc mẻ trên ERP sau khi DROP và cân thành phẩm.'))
        return False
    dialog = finish_dialog.FinishRoundDialog(aw, aw, batch=b, powder_color=_powder_color(aw))
    try:
        dialog.exec()
        finished, over, need_login = dialog.result_round, dialog.already_over, dialog.need_login
    finally:
        dialog.deleteLater()
    if need_login:
        return False
    if finished is not None:
        _set_weight(aw, output_kg=finished.output_kg)
        _flow.batch = None
        _changed()
        _report(aw, finished)
        _after_finished(aw, b)
        return True
    if over:
        _flow.batch = None
        _changed()
        _info(aw, QApplication.translate('ERP', 'Mẻ {0} đã được kết thúc hoặc huỷ ở nơi khác trên ERP.').format(b.label))
        return True
    return False


def _report(aw:'ApplicationWindow', finished:rounds.FinishedRound) -> None:
    text = QApplication.translate('ERP', 'Đã kết thúc mẻ trên ERP: {0} kg thành phẩm, hao hụt {1}%').format(
        start_dialog.format_kg(finished.output_kg), start_dialog.format_kg(round(finished.loss_percent, 1)))
    try:
        aw.sendmessage(text)
    except Exception as e: # pylint: disable=broad-except
        _log.exception(e)
    if finished.loss_exceeded:
        deviation = finish_dialog.deviation_percent(finished.output_kg, finished.expected_output_kg)
        _info(aw, QApplication.translate('ERP', 'Sản lượng {0} kg lệch {1}% so với dự kiến {2} kg, vượt ngưỡng cho phép (±{3}%).').format(
            start_dialog.format_kg(finished.output_kg), ('+' if deviation > 0 else '') + start_dialog.format_kg(round(deviation, 1)),
            start_dialog.format_kg(finished.expected_output_kg), start_dialog.format_kg(finished.tolerance_percent)),
              QApplication.translate('ERP', 'ERP đã gửi cảnh báo tới quản lý. Nếu bạn nhập nhầm số kg, hãy báo quản lý sửa trên ERP.'))


def _after_finished(aw:'ApplicationWindow', b:Batch) -> None:
    """The ERP took the weight: now the files of the batch (.alog, PDF) follow. While the roast is still recording they would be incomplete,
    so they are built when it stops."""
    if _flow.queue is None:
        return
    if _recording(aw):
        _flow.files_wait = b
        return
    _enqueue_files(aw, b)


def _enqueue_files(aw:'ApplicationWindow', b:Batch) -> None:
    queue = _flow.queue
    if queue is None:
        return
    if profile_link(aw).get('erpRoundId') != b.round_id:
        _log.info('the recording shown is not the one of batch %s: its files are not built', b.round_id)
        return # RESET or another profile since: building them now would send the wrong roast
    try:
        jobs = roast_files.build_and_enqueue(aw, b, queue, busy=roast_files.wait_cursor())
    except OSError as e:
        _log.exception(e)
        _info(aw, QApplication.translate('ERP', 'Không lưu được tệp của mẻ {0} để gửi lên ERP.').format(b.label), str(e))
        return
    uploader = _flow.uploader
    if jobs and uploader is not None:
        uploader.wake()
    _changed()


def roast_stopped(aw:'ApplicationWindow') -> None:
    """Called when Artisan stopped recording: a batch still open is finished now, and the files of a batch finished meanwhile are built
    (after a short delay, once Artisan is done stopping)."""
    if _flow.batch is None and _flow.files_wait is None:
        return

    def later() -> None:
        if current_batch() is not None: # it may have been finished or cancelled meanwhile
            finish_batch(aw, force=True)
        waiting, _flow.files_wait = _flow.files_wait, None
        if waiting is not None:
            _enqueue_files(aw, waiting)

    QTimer.singleShot(STOP_PROMPT_DELAY_MS, later)


def _cancel_on_erp(aw:'ApplicationWindow', round_id:str, reason:str) -> bool:
    try:
        # not to be given up: Artisan must know whether the ERP cancelled the batch before it resets
        background.call_with_wait(aw, QApplication.translate('ERP', 'Đang huỷ mẻ trên ERP…'), lambda: rounds.cancel_round(round_id, reason=reason))
        return True
    except rounds.RoundNotRunning:
        return True # it is over anyway
    except roasters.RoasterError as e:
        _info(aw, QApplication.translate('ERP', 'Không huỷ được mẻ trên ERP.'), e.message)
        return False


def before_reset(aw:'ApplicationWindow') -> bool:
    """RESET pressed. With a batch open the operator chooses: cancel it on the ERP (then Artisan resets) or keep it (Artisan does not reset)."""
    b = _flow.batch
    if b is None:
        return True
    choice = _choose(
        aw, QApplication.translate('ERP', 'Mẻ {0} đang chạy trên ERP.').format(b.label),
        QApplication.translate('ERP', 'RESET sẽ xoá đường rang đang hiển thị. Bạn muốn huỷ mẻ này trên ERP (ví dụ rang hỏng) hay giữ lại để kết thúc sau khi cân thành phẩm?'),
        [QApplication.translate('ERP', 'Giữ mẻ, không RESET'), QApplication.translate('ERP', 'Huỷ mẻ trên ERP và RESET')])
    if choice != 1:
        return False
    reason = _ask_reason(aw)
    if reason is None or not _cancel_on_erp(aw, b.round_id, reason):
        return False
    _flow.batch = None
    _flow.linked = None
    _flow.linked_epoch = None
    _flow.files_wait = None
    _changed()
    return True


# ---- quitting / logging out -------------------------------------------------------------------------------------------------

def confirm_quit(aw:'ApplicationWindow') -> bool:
    """True if Artisan may close. A batch that is still open stays 'running' on the ERP and keeps the roaster busy: the operator is told."""
    b = _flow.batch
    if b is None:
        return True
    choice = _choose(
        aw, QApplication.translate('ERP', 'Mẻ {0} chưa được kết thúc trên ERP.').format(b.label),
        QApplication.translate('ERP', 'Nếu thoát bây giờ, mẻ vẫn ở trạng thái “Đang chạy” trên ERP và máy rang bị coi là bận. Lần sau mở Artisan, bạn sẽ được hỏi kết thúc hay huỷ mẻ này.'),
        [QApplication.translate('ERP', 'Ở lại'), QApplication.translate('ERP', 'Thoát vẫn giữ mẻ')])
    return choice == 1


# ---- the profile ------------------------------------------------------------------------------------------------------------

def profile_link(aw:'ApplicationWindow') -> dict[str, Any]:
    """The keys to write into a profile (.alog) so that it can be tied to the batch it recorded; {} for any other profile."""
    b = _flow.linked
    if b is None or _flow.linked_epoch is None:
        return {}
    if int(getattr(aw.qmc, 'roastepoch', 0) or 0) != _flow.linked_epoch:
        return {} # a different recording is shown now
    return b.profile_link()
