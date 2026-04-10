import json
import os
import runpy
from pathlib import Path


HOOK_PATH = Path(__file__).resolve().parents[2] / 'pyinstaller_hooks' / 'rthooks' / 'pyi_rth_plus_config.py'
SRC_ROOT = Path(__file__).resolve().parents[2]
ENV_FILENAME = 'artisan-plus-env.json'
SPEC_FILES = [
    SRC_ROOT / 'artisan-linux.spec',
    SRC_ROOT / 'artisan-mac.spec',
    SRC_ROOT / 'artisan-mac_universal.spec',
    SRC_ROOT / 'artisan-win.spec',
]


def test_production_pyinstaller_specs_do_not_bundle_localhost_plus_env() -> None:
    for spec_file in SPEC_FILES:
        assert ENV_FILENAME not in spec_file.read_text(encoding='utf-8')


def test_plus_runtime_hook_does_nothing_when_packaged_env_file_is_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys._MEIPASS', str(tmp_path), raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_API_BASE_URL', raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_WEB_BASE_URL', raising=False)

    runpy.run_path(str(HOOK_PATH), run_name='__main__')

    assert 'ARTISAN_PLUS_API_BASE_URL' not in os.environ
    assert 'ARTISAN_PLUS_WEB_BASE_URL' not in os.environ



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


def test_plus_runtime_hook_skips_when_not_frozen(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ENV_FILENAME
    env_file.write_text(json.dumps({
        'ARTISAN_PLUS_API_BASE_URL': 'https://api.packaged.example/api/v3',
        'ARTISAN_PLUS_WEB_BASE_URL': 'https://web.packaged.example/app',
    }), encoding='utf-8')

    monkeypatch.setattr('sys.frozen', False, raising=False)
    monkeypatch.setattr('sys._MEIPASS', str(tmp_path), raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_API_BASE_URL', raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_WEB_BASE_URL', raising=False)

    runpy.run_path(str(HOOK_PATH), run_name='__main__')

    assert 'ARTISAN_PLUS_API_BASE_URL' not in os.environ
    assert 'ARTISAN_PLUS_WEB_BASE_URL' not in os.environ


def test_plus_runtime_hook_ignores_non_dict_payload(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ENV_FILENAME
    env_file.write_text(json.dumps(['not', 'a', 'dict']), encoding='utf-8')

    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys._MEIPASS', str(tmp_path), raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_API_BASE_URL', raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_WEB_BASE_URL', raising=False)

    runpy.run_path(str(HOOK_PATH), run_name='__main__')

    assert 'ARTISAN_PLUS_API_BASE_URL' not in os.environ
    assert 'ARTISAN_PLUS_WEB_BASE_URL' not in os.environ


def test_plus_runtime_hook_ignores_empty_and_non_string_values(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ENV_FILENAME
    env_file.write_text(json.dumps({
        'ARTISAN_PLUS_API_BASE_URL': '',
        'ARTISAN_PLUS_WEB_BASE_URL': 123,
    }), encoding='utf-8')

    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys._MEIPASS', str(tmp_path), raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_API_BASE_URL', raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_WEB_BASE_URL', raising=False)

    runpy.run_path(str(HOOK_PATH), run_name='__main__')

    assert 'ARTISAN_PLUS_API_BASE_URL' not in os.environ
    assert 'ARTISAN_PLUS_WEB_BASE_URL' not in os.environ


def test_plus_runtime_hook_logs_invalid_json(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ENV_FILENAME
    env_file.write_text('{invalid json', encoding='utf-8')

    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys._MEIPASS', str(tmp_path), raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_API_BASE_URL', raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_WEB_BASE_URL', raising=False)

    with monkeypatch.context() as context:
        import logging
        logger = logging.getLogger('__main__')
        with patch.object(logger, 'warning') as mock_warning:
            runpy.run_path(str(HOOK_PATH), run_name='__main__')

        mock_warning.assert_called_once()
        assert env_file.name in mock_warning.call_args.args[1]

    assert 'ARTISAN_PLUS_API_BASE_URL' not in os.environ
    assert 'ARTISAN_PLUS_WEB_BASE_URL' not in os.environ


def test_plus_runtime_hook_keeps_valid_string_values_only(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ENV_FILENAME
    env_file.write_text(json.dumps({
        'ARTISAN_PLUS_API_BASE_URL': 'https://api.packaged.example/api/v3',
        'ARTISAN_PLUS_WEB_BASE_URL': '',
        'IGNORED': 'value',
    }), encoding='utf-8')

    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys._MEIPASS', str(tmp_path), raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_API_BASE_URL', raising=False)
    monkeypatch.delenv('ARTISAN_PLUS_WEB_BASE_URL', raising=False)

    runpy.run_path(str(HOOK_PATH), run_name='__main__')

    assert os.environ['ARTISAN_PLUS_API_BASE_URL'] == 'https://api.packaged.example/api/v3'
    assert 'ARTISAN_PLUS_WEB_BASE_URL' not in os.environ


from unittest.mock import patch
