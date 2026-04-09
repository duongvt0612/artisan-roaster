import json
import os
import runpy
from pathlib import Path


HOOK_PATH = Path(__file__).resolve().parents[2] / 'pyinstaller_hooks' / 'rthooks' / 'pyi_rth_plus_config.py'
ENV_FILENAME = 'artisan-plus-env.json'


def test_plus_runtime_hook_loads_frozen_env_defaults(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ENV_FILENAME
    env_file.write_text(json.dumps({
        'ARTISAN_PLUS_API_BASE_URL': 'https://api.packaged.example/api/v3',
        'ARTISAN_PLUS_WEB_BASE_URL': 'https://web.packaged.example/app',
    }), encoding='utf-8')

    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys._MEIPASS', str(tmp_path), raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_API_BASE_URL', raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_WEB_BASE_URL', raising=False)

    runpy.run_path(str(HOOK_PATH), run_name='__main__')

    assert os.environ['ARTISAN_PLUS_API_BASE_URL'] == 'https://api.packaged.example/api/v3'
    assert os.environ['ARTISAN_PLUS_WEB_BASE_URL'] == 'https://web.packaged.example/app'


def test_plus_runtime_hook_does_not_leak_env_between_tests(monkeypatch) -> None:
    monkeypatch.delenv('ARTISAN_PLUS_API_BASE_URL', raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_WEB_BASE_URL', raising=False)

    assert 'ARTISAN_PLUS_API_BASE_URL' not in os.environ
    assert 'ARTISAN_PLUS_WEB_BASE_URL' not in os.environ


def test_plus_runtime_hook_preserves_runtime_env_over_packaged_defaults(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ENV_FILENAME
    env_file.write_text(json.dumps({
        'ARTISAN_PLUS_API_BASE_URL': 'https://api.packaged.example/api/v3',
        'ARTISAN_PLUS_WEB_BASE_URL': 'https://web.packaged.example/app',
    }), encoding='utf-8')

    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys._MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setenv('ARTISAN_PLUS_API_BASE_URL', 'https://runtime.example/api')
    monkeypatch.setenv('ARTISAN_PLUS_WEB_BASE_URL', 'https://runtime.example/app')

    runpy.run_path(str(HOOK_PATH), run_name='__main__')

    assert os.environ['ARTISAN_PLUS_API_BASE_URL'] == 'https://runtime.example/api'
    assert os.environ['ARTISAN_PLUS_WEB_BASE_URL'] == 'https://runtime.example/app'
