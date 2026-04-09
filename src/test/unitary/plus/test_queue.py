import importlib
import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch


class MockQSemaphore:
    def __init__(self, _count: int = 1) -> None:
        self.acquire = Mock()
        self.release = Mock()
        self.available = Mock(return_value=1)


class MockQCoreApplication:
    @staticmethod
    def instance() -> None:
        return None


class MockQObject:
    pass


class MockQThread:
    def start(self) -> None:
        return None


class MockQApplication:
    @staticmethod
    def translate(_context: str, text: str) -> str:
        return text


def mock_pyqt_slot(*_args: object, **_kwargs: object):
    return lambda func: func


def mock_pyqt_signal(*_args: object, **_kwargs: object) -> Mock:
    return Mock()


@contextmanager
def load_queue_module():
    mocked_modules = {
        'PyQt6.QtCore': SimpleNamespace(
            QCoreApplication=MockQCoreApplication,
            QObject=MockQObject,
            QThread=MockQThread,
            pyqtSlot=mock_pyqt_slot,
            pyqtSignal=mock_pyqt_signal,
            QSemaphore=MockQSemaphore,
        ),
        'PyQt6.QtWidgets': SimpleNamespace(QApplication=MockQApplication),
        'artisanlib.util': SimpleNamespace(getDirectory=lambda *_args, **_kwargs: '/tmp/test-outbox'),
        'plus.util': Mock(),
        'plus.roast': Mock(),
        'plus.connection': Mock(),
        'plus.sync': Mock(),
        'plus.controller': Mock(),
    }

    with patch.dict(sys.modules, mocked_modules, clear=False):
        from plus import config

        importlib.reload(config)
        sys.modules.pop('plus.queue', None)
        queue_module = importlib.import_module('plus.queue')
        yield queue_module, config


def test_send_lock_schedule_uses_env_driven_lock_schedule_prefix() -> None:
    original_api = os.environ.get('ARTISAN_PLUS_API_BASE_URL')
    original_web = os.environ.get('ARTISAN_PLUS_WEB_BASE_URL')

    try:
        os.environ['ARTISAN_PLUS_API_BASE_URL'] = 'https://staging.example.test/api/v2/'
        os.environ['ARTISAN_PLUS_WEB_BASE_URL'] = 'https://staging.example.test/app/'

        with load_queue_module() as (queue_module, config):
            queued_items = Mock()
            app_window = Mock()
            app_window.plus_readonly = False
            app_window.qmc.registerLockScheduleSent = Mock()

            queue_module.queue = queued_items
            queue_module.config.app_window = app_window

            queue_module.sendLockSchedule()

            queued_item = queued_items.put.call_args.args[0]
            assert queued_item['verb'] == 'POST'
            assert queued_item['data'] == {}
            assert queued_item['url'].startswith(config.lock_schedule_url)
            assert queued_item['url'].startswith('https://staging.example.test/api/v2/aschedule/lock')
            assert '?today=' in queued_item['url']
            app_window.qmc.registerLockScheduleSent.assert_called_once_with()
    finally:
        if original_api is None:
            os.environ.pop('ARTISAN_PLUS_API_BASE_URL', None)
        else:
            os.environ['ARTISAN_PLUS_API_BASE_URL'] = original_api

        if original_web is None:
            os.environ.pop('ARTISAN_PLUS_WEB_BASE_URL', None)
        else:
            os.environ['ARTISAN_PLUS_WEB_BASE_URL'] = original_web

        from plus import config

        importlib.reload(config)
        sys.modules.pop('plus.queue', None)
