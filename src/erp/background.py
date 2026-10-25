#!/usr/bin/python
#
# background.py
#
# ABOUT
# Runs a call to the ERP away from the GUI thread, for the places where Artisan cannot use a dialog of its own:
#   * run_in_thread(): fire and forget, the answer is handed to a callback on the GUI thread;
#   * call_with_wait(): for a step that cannot go on without the answer (RESET, quit, ...). The caller still gets a plain
#     return value (or the exception of the call), but the event loop keeps running while it waits, and a small "please wait"
#     box shows up if the answer takes longer than a moment. Nothing freezes if the network is slow.

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
from typing import Any, Final, override

from PyQt6.QtCore import QEventLoop, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

_log: Final[logging.Logger] = logging.getLogger(__name__)

# the wait box only shows up if the answer takes longer than this: a fast network never flashes it
SHOW_AFTER_MS: Final[int] = 250


class Cancelled(Exception):
    """The operator gave up waiting (only possible if the call is read-only and `cancellable` was set)."""


class TimedOut(Cancelled):
    """The call did not answer in the time the caller was willing to wait."""


class _Bridge(QObject):
    """Delivers the outcome of a call made in a thread to the GUI thread: (result, error)."""
    done = pyqtSignal(object, object)


def _start(bridge:_Bridge, work:Callable[[], Any], name:str) -> None:
    def target() -> None:
        result:Any = None
        error:BaseException|None = None
        try:
            result = work()
        except Exception as e: # pylint: disable=broad-except
            error = e
        try:
            bridge.done.emit(result, error)
        except RuntimeError:
            _log.debug('answer of %s dropped: nobody is waiting any more', name)

    threading.Thread(target=target, name=name, daemon=True).start()


_alive:set[_Bridge] = set() # run_in_thread() has no owner: keeps the bridge from being collected before its answer arrived


def idle() -> bool:
    """True if every call made with run_in_thread() has been answered (and its callback run)."""
    return not _alive


def run_in_thread(work:Callable[[], Any], on_done:Callable[[Any, BaseException|None], None], *, name:str = 'erp-background') -> None:
    """Runs `work()` in a thread; then `on_done(result, error)` is called on the GUI thread (error is None if it worked).

    Must be called from the GUI thread. Without a running QApplication the work is simply done in place."""
    if QApplication.instance() is None:
        try:
            result, error = work(), None
        except Exception as e: # pylint: disable=broad-except
            result, error = None, e
        on_done(result, error)
        return
    bridge = _Bridge()
    _alive.add(bridge)

    def finished(result:Any, error:Any) -> None:
        _alive.discard(bridge)
        bridge.deleteLater()
        on_done(result, error)

    bridge.done.connect(finished)
    _start(bridge, work, name)


class _WaitDialog(QDialog):

    def __init__(self, parent:QWidget|None, text:str, cancellable:bool) -> None:
        super().__init__(parent)
        self.cancellable:bool = cancellable
        self.setWindowTitle('Artisan')
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, cancellable)
        self.setMinimumWidth(320)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAccessibleName(text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.addWidget(label)
        if cancellable:
            skip = QPushButton(QApplication.translate('ERP', 'Bỏ qua'))
            skip.setAutoDefault(False)
            skip.clicked.connect(self.reject)
            layout.addWidget(skip, 0, Qt.AlignmentFlag.AlignHCenter)

    @override
    def reject(self) -> None:
        if self.cancellable:
            super().reject()

    @override
    def closeEvent(self, event:QCloseEvent|None) -> None:
        if event is None:
            return
        if self.cancellable:
            super().closeEvent(event)
        else:
            event.ignore() # the answer is awaited: the box closes by itself


def _call_bounded(work:Callable[[], Any], timeout:float) -> Any:
    """call_with_wait() without an application (nothing to keep running): the wait is still bounded."""
    outcome:list[tuple[Any, BaseException|None]] = []

    def target() -> None:
        try:
            outcome.append((work(), None))
        except Exception as e: # pylint: disable=broad-except
            outcome.append((None, e))

    thread = threading.Thread(target=target, name='erp-wait', daemon=True)
    thread.start()
    thread.join(timeout)
    if not outcome:
        raise TimedOut()
    result, error = outcome[0]
    if error is not None:
        raise error
    return result


def call_with_wait(parent:QWidget|None, text:str, work:Callable[[], Any], *, cancellable:bool = False, show_after_ms:int = SHOW_AFTER_MS,
                   timeout:float|None = None) -> Any:
    """Returns what `work()` returns, or raises what it raises, without freezing the application while it waits.

    `cancellable` adds a way to give up (raises Cancelled): only for calls that change nothing on the ERP.
    `timeout` (seconds) is how long to wait at most (raises TimedOut, a Cancelled); the call itself cannot be stopped and goes on in its thread.
    Without a running QApplication the work is simply done in place."""
    if QApplication.instance() is None:
        return work() if timeout is None else _call_bounded(work, timeout)
    loop = QEventLoop()
    outcome:list[Any] = []
    bridge = _Bridge()

    def finished(result:Any, error:Any) -> None:
        outcome.append((result, error))
        loop.quit()

    bridge.done.connect(finished)
    dialog = _WaitDialog(parent if isinstance(parent, QWidget) else None, text, cancellable)
    dialog.rejected.connect(loop.quit)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(dialog.show)
    timer.start(show_after_ms)
    expired:list[bool] = []
    limit:QTimer|None = None
    if timeout is not None:
        limit = QTimer()
        limit.setSingleShot(True)
        limit.timeout.connect(lambda: (expired.append(True), loop.quit()))
        limit.start(int(timeout * 1000))
    _start(bridge, work, 'erp-wait')
    try:
        if not outcome: # the answer may already be in
            loop.exec()
    finally:
        timer.stop()
        if limit is not None:
            limit.stop()
        dialog.hide()
        dialog.deleteLater()
    if not outcome:
        raise (TimedOut() if expired else Cancelled()) # the thread goes on; its answer is dropped
    result, error = outcome[0]
    if error is not None:
        raise error
    return result
