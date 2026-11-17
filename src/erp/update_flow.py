#!/usr/bin/python
#
# update_flow.py
#
# ABOUT
# When and how Artisan updates itself (the mechanics are in updater.py):
#   * started():  right after the main window exists, before the login: marks this version healthy for the launcher and removes old versions;
#   * after_login(): asks the ERP for the newest release. A forced one (isForceUpdate) is downloaded right away behind a blocking progress
#     box and Artisan restarts into it before a roaster is chosen; any other one is downloaded in the background and used at the next start;
#   * check_now(): the "Check for Updates" menu item.
# None of this ever happens during a roast: a release goes into a folder of its own and only the next start uses it.

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
from pathlib import Path
from typing import Any, Final, cast

from PyQt6.QtCore import QEventLoop, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from erp import background, client, updater

_log: Final[logging.Logger] = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS: Final[float] = 10.0


def _tr(text:str) -> str:
    return QApplication.translate('ERP', text)


def _notify(window:Any, text:str) -> None:
    """A line in the message bar of the main window (anything with sendmessage()), else only the log."""
    _log.info('%s', text)
    send = getattr(window, 'sendmessage', None)
    if callable(send):
        try:
            send(text)
        except Exception as e: # pylint: disable=broad-except
            _log.debug('sendmessage failed: %s', e)


def started() -> None:
    """Right after start: this version runs (the launcher keeps it), and the versions nobody needs any more go. Never raises."""
    if not updater.enabled():
        return
    updater.mark_healthy()
    root = updater.launcher_root()
    if root is not None:
        threading.Thread(target=updater.cleanup, args=(root,), name='erp-update-cleanup', daemon=True).start()


def _fetch(parent:QWidget|None) -> updater.Release|None:
    """The newest release, None if there is none or the ERP could not tell (an update check never keeps anybody from roasting)."""
    try:
        return cast(updater.Release|None, background.call_with_wait(parent, _tr('Đang kiểm tra bản cập nhật...'), updater.fetch_latest,
                                                                     timeout=CHECK_TIMEOUT_SECONDS))
    except background.Cancelled:
        _log.info('update check timed out')
    except (client.ErpAuthError, updater.UpdateError) as e:
        _log.warning('update check failed: %s', getattr(e, 'message', e))
    return None


def _download_with_progress(parent:QWidget|None, release:updater.Release, root:Path) -> None:
    """stage() behind a modal progress box; the event loop keeps running. Raises what stage() raises (UpdateCancelled if the user gave up)."""
    box = QProgressDialog(_tr('Đang tải bản cập nhật {0}...').format(release.version_name), _tr('Huỷ'), 0, 100,
                          parent if isinstance(parent, QWidget) else None)
    box.setWindowTitle('Artisan')
    box.setWindowModality(Qt.WindowModality.ApplicationModal)
    box.setMinimumDuration(0)
    box.setAutoClose(False)
    box.setAutoReset(False)
    box.setValue(0)
    state:dict[str, Any] = {'done': 0, 'total': 0, 'cancel': False}
    outcome:list[BaseException|None] = []
    loop = QEventLoop()

    def progress(done:int, total:int) -> None:
        state['done'], state['total'] = done, total

    def work() -> None:
        try:
            updater.stage(root, release, progress, lambda: bool(state['cancel']))
            outcome.append(None)
        except BaseException as e: # pylint: disable=broad-except # handed to the GUI thread
            outcome.append(e)

    def tick() -> None:
        total = int(state['total'])
        if total > 0:
            box.setValue(min(99, int(state['done'] * 100 / total)))
        else:
            box.setRange(0, 0) # size unknown: busy indicator
        if outcome:
            loop.quit()

    box.canceled.connect(lambda: state.update(cancel=True))
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    threading.Thread(target=work, name='erp-update-download', daemon=True).start()
    try:
        loop.exec()
    finally:
        timer.stop()
        box.hide()
        box.deleteLater()
    error = outcome[0] if outcome else None
    if error is not None:
        raise error


def _forced(parent:QWidget|None, release:updater.Release, root:Path) -> bool:
    """Installs a forced release and restarts into it. Always returns False: Artisan must end (restarting, or the user gave up)."""
    while True:
        try:
            _download_with_progress(parent, release, root)
            updater.activate(root, release)
        except updater.UpdateError as e:
            box = QMessageBox(parent if isinstance(parent, QWidget) else None)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle('Artisan')
            box.setText(_tr('Bản cập nhật {0} là bắt buộc nhưng chưa cài được.').format(release.version_name))
            box.setInformativeText(e.message)
            retry = box.addButton(_tr('Thử lại'), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(_tr('Thoát'), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is retry:
                continue
            return False
        if not updater.restart_via_launcher():
            QMessageBox.information(parent if isinstance(parent, QWidget) else None, 'Artisan',
                                    _tr('Đã cài bản cập nhật {0}. Hãy mở lại Artisan.').format(release.version_name))
        return False


def _in_background(window:Any, release:updater.Release, root:Path, on_done:Callable[[bool], None]|None = None) -> None:
    def work() -> None:
        updater.stage(root, release)
        updater.activate(root, release)

    def done(_result:Any, error:BaseException|None) -> None:
        if error is None:
            _notify(window, _tr('Đã tải bản cập nhật {0}, sẽ dùng ở lần mở Artisan sau.').format(release.version_name))
        else:
            _log.warning('background update %s failed: %s', release.version_name, getattr(error, 'message', error))
        if on_done is not None:
            on_done(error is None)

    background.run_in_thread(work, done, name='erp-update-download')


def after_login(window:Any) -> bool:
    """Called after the login, before the roaster is chosen. False: Artisan must end now (it restarts into a forced update)."""
    root = updater.launcher_root()
    if root is None:
        return True
    parent = window if isinstance(window, QWidget) else None
    release = _fetch(parent)
    if release is None or not updater.is_newer(release):
        return True
    if updater.is_blocked(root, release):
        _log.warning('update %s was rolled back by the launcher, waiting for a newer release', release.version_name)
        return True
    _log.info('update %s available (force=%s)', release.version_name, release.force)
    if release.force:
        return _forced(parent, release, root)
    if updater.is_staged(root, release):
        updater.activate(root, release)
    else:
        _in_background(window, release, root)
    return True


def check_now(window:Any) -> None:
    """The "Check for Updates" menu item: tells what there is, and fetches a newer release in the background (used at the next start)."""
    parent = window if isinstance(window, QWidget) else None
    root = updater.launcher_root()
    if root is None:
        QMessageBox.information(parent, _tr('Cập nhật'),
                                _tr('Bản Artisan này không chạy qua launcher nên không tự cập nhật được.'))
        return
    try:
        release = background.call_with_wait(parent, _tr('Đang kiểm tra bản cập nhật...'), updater.fetch_latest, timeout=CHECK_TIMEOUT_SECONDS)
    except background.Cancelled:
        QMessageBox.warning(parent, _tr('Cập nhật'), _tr('Máy chủ ERP không trả lời, hãy thử lại sau.'))
        return
    except (client.ErpAuthError, updater.UpdateError) as e:
        QMessageBox.warning(parent, _tr('Cập nhật'), getattr(e, 'message', str(e)))
        return
    if not updater.is_newer(release):
        QMessageBox.information(parent, _tr('Cập nhật'), _tr('Bạn đang dùng bản mới nhất.'))
        return
    assert release is not None
    if updater.is_blocked(root, release):
        QMessageBox.warning(parent, _tr('Cập nhật'),
                            _tr('Bản {0} không khởi động được trên máy này nên đã quay về bản cũ. Hãy chờ bản sửa lỗi.').format(release.version_name))
        return
    if updater.is_staged(root, release):
        updater.activate(root, release)
        QMessageBox.information(parent, _tr('Cập nhật'),
                                _tr('Bản {0} đã sẵn sàng, sẽ dùng ở lần mở Artisan sau.').format(release.version_name))
        return
    QMessageBox.information(parent, _tr('Cập nhật'),
                            _tr('Có bản mới {0}. Artisan sẽ tải ngầm và dùng ở lần mở sau.').format(release.version_name))
    _in_background(window, release, root)
