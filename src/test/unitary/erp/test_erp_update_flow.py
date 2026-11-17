"""Tests of when Artisan updates itself (erp.update_flow): a forced release restarts Artisan before a roaster is held, any other one is fetched
in the background, and a failing check never keeps anybody from roasting (real Qt, offscreen; updater calls replaced)."""

import os
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from erp import background, client, update_flow, updater  # noqa: E402


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


class Window:
    def __init__(self) -> None:
        self.messages:list[str] = []

    def sendmessage(self, text:str) -> None:
        self.messages.append(text)


def release(force:bool, code:int = 125) -> updater.Release:
    return updater.Release(code, f'4.2.1-{code}', 'https://r.example.vn/a.zip', 'a' * 64, force, '')


@pytest.fixture
def launcher(tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> dict[str, Any]:
    (tmp_path / 'versions').mkdir()
    monkeypatch.setenv(updater.ENV_LAUNCHER_ROOT, str(tmp_path))
    monkeypatch.setattr(updater.config, 'build_code', lambda: 120)
    log:dict[str, Any] = {'staged': [], 'activated': [], 'restarted': 0}
    monkeypatch.setattr(updater, 'stage', lambda _root, r, *_a, **_k: log['staged'].append(r.version_code))
    monkeypatch.setattr(updater, 'activate', lambda _root, r: log['activated'].append(r.version_code))

    def restart() -> bool:
        log['restarted'] += 1
        return True

    monkeypatch.setattr(updater, 'restart_via_launcher', restart)
    return log


def wait_until(condition:Any, seconds:float = 3.0) -> None:
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def test_without_launcher_nothing_happens(qapp:QApplication, monkeypatch:pytest.MonkeyPatch) -> None:  # noqa: ARG001
    monkeypatch.delenv(updater.ENV_LAUNCHER_ROOT, raising=False)
    monkeypatch.setattr(updater, 'fetch_latest', lambda: pytest.fail('must not ask the ERP'))
    assert update_flow.after_login(Window()) is True


def test_forced_release_installs_and_restarts(qapp:QApplication, launcher:dict[str, Any], monkeypatch:pytest.MonkeyPatch) -> None:  # noqa: ARG001
    monkeypatch.setattr(updater, 'fetch_latest', lambda: release(force=True))
    assert update_flow.after_login(Window()) is False
    assert launcher == {'staged': [125], 'activated': [125], 'restarted': 1}


def test_optional_release_is_fetched_in_the_background(qapp:QApplication, launcher:dict[str, Any], monkeypatch:pytest.MonkeyPatch) -> None:  # noqa: ARG001
    monkeypatch.setattr(updater, 'fetch_latest', lambda: release(force=False))
    window = Window()
    assert update_flow.after_login(window) is True
    wait_until(lambda: bool(window.messages) and background.idle())
    assert launcher == {'staged': [125], 'activated': [125], 'restarted': 0}
    assert '4.2.1-125' in window.messages[0]


def test_same_or_older_release_does_nothing(qapp:QApplication, launcher:dict[str, Any], monkeypatch:pytest.MonkeyPatch) -> None:  # noqa: ARG001
    monkeypatch.setattr(updater, 'fetch_latest', lambda: release(force=True, code=120))
    assert update_flow.after_login(Window()) is True
    assert launcher == {'staged': [], 'activated': [], 'restarted': 0}


def test_a_rolled_back_release_is_not_fetched_again(qapp:QApplication, launcher:dict[str, Any], monkeypatch:pytest.MonkeyPatch) -> None:  # noqa: ARG001
    monkeypatch.setattr(updater, 'fetch_latest', lambda: release(force=True))
    monkeypatch.setattr(updater, 'is_blocked', lambda _root, r: r.version_code == 125)
    assert update_flow.after_login(Window()) is True
    assert launcher == {'staged': [], 'activated': [], 'restarted': 0}


def test_a_failing_check_never_blocks(qapp:QApplication, launcher:dict[str, Any], monkeypatch:pytest.MonkeyPatch) -> None:  # noqa: ARG001
    def down() -> None:
        raise client.ErpAuthError('network', 'down')

    monkeypatch.setattr(updater, 'fetch_latest', down)
    assert update_flow.after_login(Window()) is True
    assert launcher['staged'] == []


def test_forced_release_that_fails_lets_the_user_quit(qapp:QApplication, launcher:dict[str, Any], monkeypatch:pytest.MonkeyPatch) -> None:  # noqa: ARG001
    monkeypatch.setattr(updater, 'fetch_latest', lambda: release(force=True))

    def broken(*_a:Any, **_k:Any) -> None:
        raise updater.UpdateError('hỏng')

    monkeypatch.setattr(updater, 'stage', broken)
    shown:list[str] = []

    def answer(box:QMessageBox) -> int:
        shown.append(box.informativeText())
        return 0 # no button clicked: clickedButton() is not "retry" -> quit

    monkeypatch.setattr(QMessageBox, 'exec', answer)
    assert update_flow.after_login(Window()) is False
    assert shown == ['hỏng']
    assert launcher['restarted'] == 0
