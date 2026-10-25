"""Unit tests for erp.background: ERP calls away from the GUI thread (real Qt, offscreen)."""

import os
import threading
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from erp import background  # noqa: E402


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


class Ticker:
    """Counts the turns of the event loop while a slow call is awaited: proves that the GUI does not freeze."""

    def __init__(self) -> None:
        self.ticks = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(10)

    def _tick(self) -> None:
        self.ticks += 1

    def stop(self) -> None:
        self.timer.stop()


def slow(seconds:float, value:Any = 'ok') -> Any:
    time.sleep(seconds)
    return value


class TestCallWithWait:
    def test_returns_the_result(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert background.call_with_wait(None, 'Đang…', lambda: 42) == 42

    def test_raises_what_the_call_raises(self, qapp:QApplication) -> None:  # noqa: ARG002
        def boom() -> None:
            raise ValueError('hỏng')
        with pytest.raises(ValueError, match='hỏng'):
            background.call_with_wait(None, 'Đang…', boom)

    def test_the_call_runs_off_the_gui_thread(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert background.call_with_wait(None, 'Đang…', threading.current_thread) is not threading.main_thread()

    def test_the_event_loop_keeps_running_while_waiting(self, qapp:QApplication) -> None:  # noqa: ARG002
        ticker = Ticker()
        try:
            assert background.call_with_wait(None, 'Đang…', lambda: slow(0.4)) == 'ok'
        finally:
            ticker.stop()
        assert ticker.ticks >= 10  # ~40 turns expected in 0.4 s: a frozen GUI would have none

    def test_a_fast_call_never_shows_the_box(self, qapp:QApplication) -> None:  # noqa: ARG002
        with patch.object(background._WaitDialog, 'show') as show:  # pylint: disable=protected-access
            background.call_with_wait(None, 'Đang…', lambda: 1)
        show.assert_not_called()

    def test_a_slow_call_shows_the_box_then_closes_it(self, qapp:QApplication) -> None:  # noqa: ARG002
        seen:list[bool] = []
        original = background._WaitDialog.show  # pylint: disable=protected-access

        def spy(dialog:Any) -> None:
            original(dialog)
            seen.append(dialog.isVisible())

        with patch.object(background._WaitDialog, 'show', spy):  # pylint: disable=protected-access
            background.call_with_wait(None, 'Đang…', lambda: slow(0.5), show_after_ms=50)
        assert seen == [True]
        assert not [w for w in QApplication.topLevelWidgets() if w.isVisible()]

    def test_the_box_cannot_be_closed_if_the_call_changes_something(self, qapp:QApplication) -> None:  # noqa: ARG002
        dialog = background._WaitDialog(None, 'Đang…', False)  # pylint: disable=protected-access
        dialog.show()
        dialog.reject()
        assert dialog.isVisible()
        dialog.close()
        assert dialog.isVisible()
        dialog.hide()

    def test_giving_up_raises_cancelled(self, qapp:QApplication) -> None:  # noqa: ARG002
        def press_skip() -> None:
            for w in QApplication.topLevelWidgets():
                if isinstance(w, background._WaitDialog) and w.isVisible():  # pylint: disable=protected-access
                    w.reject()

        QTimer.singleShot(150, press_skip)
        started = time.monotonic()
        with pytest.raises(background.Cancelled):
            background.call_with_wait(None, 'Đang…', lambda: slow(1.0), cancellable=True, show_after_ms=20)
        assert time.monotonic() - started < 0.9  # did not wait for the answer

    def test_a_call_that_takes_too_long_raises_timed_out(self, qapp:QApplication) -> None:  # noqa: ARG002
        started = time.monotonic()
        with pytest.raises(background.TimedOut):
            background.call_with_wait(None, 'Đang…', lambda: slow(1.0), timeout=0.2)
        assert time.monotonic() - started < 0.8
        assert isinstance(background.TimedOut(), background.Cancelled)  # whoever handles "gave up" handles this too

    def test_a_call_inside_the_timeout_returns_normally(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert background.call_with_wait(None, 'Đang…', lambda: slow(0.05, 9), timeout=2) == 9

    def test_an_answer_after_giving_up_is_harmless(self, qapp:QApplication) -> None:
        QTimer.singleShot(100, lambda: [w.reject() for w in QApplication.topLevelWidgets() if isinstance(w, background._WaitDialog)])  # pylint: disable=protected-access
        with pytest.raises(background.Cancelled):
            background.call_with_wait(None, 'Đang…', lambda: slow(0.3), cancellable=True, show_after_ms=10)
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            qapp.processEvents()  # the late answer arrives here and must not raise
            time.sleep(0.01)

    def test_a_parent_that_is_not_a_widget_is_ignored(self, qapp:QApplication) -> None:  # noqa: ARG002
        # the callers pass Artisan's main window; tests and tools may pass anything
        assert background.call_with_wait(object(), 'Đang…', lambda: 1) == 1  # type:ignore[arg-type]

    def test_without_an_application_the_work_is_done_in_place(self) -> None:
        with patch.object(background.QApplication, 'instance', return_value=None):
            assert background.call_with_wait(None, 'Đang…', lambda: 7) == 7

    def test_without_an_application_the_wait_is_still_bounded(self) -> None:
        started = time.monotonic()
        with patch.object(background.QApplication, 'instance', return_value=None), pytest.raises(background.TimedOut):
            background.call_with_wait(None, 'Đang…', lambda: slow(1.0), timeout=0.1)
        assert time.monotonic() - started < 0.8


class TestRunInThread:
    def test_idle_tells_when_every_answer_was_handled(self, qapp:QApplication) -> None:
        assert background.idle()
        background.run_in_thread(lambda: slow(0.1), lambda _r, _e: None)
        assert not background.idle()
        end = time.monotonic() + 2
        while not background.idle() and time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert background.idle()

    def test_the_answer_comes_back_on_the_gui_thread(self, qapp:QApplication) -> None:
        got:list[tuple[Any, Any, threading.Thread]] = []
        background.run_in_thread(lambda: slow(0.05, 5), lambda r, e: got.append((r, e, threading.current_thread())))
        end = time.monotonic() + 2
        while not got and time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert got and got[0][0] == 5 and got[0][1] is None and got[0][2] is threading.main_thread()

    def test_the_error_comes_back_too(self, qapp:QApplication) -> None:
        got:list[Any] = []

        def boom() -> None:
            raise ValueError('hỏng')

        background.run_in_thread(boom, lambda r, e: got.append((r, e)))
        end = time.monotonic() + 2
        while not got and time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert got and got[0][0] is None and isinstance(got[0][1], ValueError)

    def test_does_not_block_the_caller(self, qapp:QApplication) -> None:
        started = time.monotonic()
        background.run_in_thread(lambda: slow(0.5), lambda _r, _e: None)
        assert time.monotonic() - started < 0.2
        end = time.monotonic() + 3
        while not background.idle() and time.monotonic() < end:  # the answer must arrive while this QApplication is still there
            qapp.processEvents()
            time.sleep(0.01)
        assert background.idle()

    def test_without_an_application_the_work_is_done_in_place(self) -> None:
        got:list[Any] = []
        with patch.object(background.QApplication, 'instance', return_value=None):
            background.run_in_thread(lambda: 3, lambda r, e: got.append((r, e)))
        assert got == [(3, None)]
