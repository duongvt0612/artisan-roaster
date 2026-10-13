"""Tests of how the ERP server gets into a build: make-erp-config.py writes erp-config.json, the build scripts call it and the
PyInstaller specs refuse to build without it. The repo never tracks a real erp-config.json (that would be a hardcoded server)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from erp import config

SRC = Path(__file__).resolve().parents[3]
SCRIPT = SRC / 'make-erp-config.py'
SPECS = [SRC / 'artisan-linux.spec', SRC / 'artisan-mac.spec', SRC / 'artisan-win.spec']
BUILD_SCRIPTS = [SRC / 'build-linux.sh', SRC / 'build-macos3.sh', SRC / 'build-win3-pi.bat']


def run(tmp_path:Path, *args:str, env:dict[str, str]|None = None) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if k not in (config.ENV_API_URL, config.ENV_ENVIRONMENT, config.ENV_SOCKET_URL)}
    clean.update(env or {})
    return subprocess.run([sys.executable, str(SCRIPT), '--out', str(tmp_path / 'erp-config.json'), *args],
                          capture_output=True, text=True, env=clean, check=False, cwd=str(tmp_path))


class TestMakeErpConfig:
    def test_writes_the_file_the_app_reads(self, tmp_path:Path) -> None:
        r = run(tmp_path, '--url', 'https://erp-api.example.vn/api/', '--env', 'Production')
        assert r.returncode == 0, r.stderr
        data = json.loads((tmp_path / 'erp-config.json').read_text(encoding='utf-8'))
        assert data == {'apiBaseUrl': 'https://erp-api.example.vn/api', 'environment': 'production'}

    def test_takes_the_values_from_the_environment_variables_of_the_build(self, tmp_path:Path) -> None:
        r = run(tmp_path, env={config.ENV_API_URL: 'https://stg.example.vn/api', config.ENV_ENVIRONMENT: 'staging',
                               config.ENV_SOCKET_URL: 'https://rt.example.vn'})
        assert r.returncode == 0, r.stderr
        assert json.loads((tmp_path / 'erp-config.json').read_text(encoding='utf-8')) == {
            'apiBaseUrl': 'https://stg.example.vn/api', 'environment': 'staging', 'socketUrl': 'https://rt.example.vn'}

    def test_the_file_it_writes_is_accepted_by_the_runtime(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        assert run(tmp_path, '--url', 'https://erp-api.example.vn/api', '--env', 'staging').returncode == 0
        for name in (config.ENV_API_URL, config.ENV_ENVIRONMENT, config.ENV_SOCKET_URL):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(config, '_config_file', lambda: tmp_path / 'erp-config.json')
        config.reset_cache()
        assert config.get_api_base_url() == 'https://erp-api.example.vn/api' and config.environment() == 'staging'

    def test_there_is_no_default_server(self, tmp_path:Path) -> None:
        r = run(tmp_path, '--env', 'production')
        assert r.returncode != 0 and 'No ERP server' in r.stderr
        assert not (tmp_path / 'erp-config.json').exists()

    def test_there_is_no_default_environment(self, tmp_path:Path) -> None:
        r = run(tmp_path, '--url', 'https://erp-api.example.vn/api')
        assert r.returncode != 0 and 'No environment' in r.stderr
        assert not (tmp_path / 'erp-config.json').exists()

    @pytest.mark.parametrize(('url', 'env'), [
        ('http://erp.example.vn/api', 'production'),   # plain http to another machine
        ('erp.example.vn/api', 'production'),
        ('https://erp.example.vn/api', 'Pro duction'),
        ('https://erp.example.vn/api', 'x' * 21),
    ])
    def test_invalid_values_stop_the_build(self, tmp_path:Path, url:str, env:str) -> None:
        r = run(tmp_path, '--url', url, '--env', env)
        assert r.returncode == 2 and r.stderr.startswith('make-erp-config:')
        assert not (tmp_path / 'erp-config.json').exists()

    def test_http_localhost_is_fine_for_a_development_build(self, tmp_path:Path) -> None:
        assert run(tmp_path, '--url', 'http://localhost:10301/api', '--env', 'dev').returncode == 0


class TestRepository:
    def test_the_repo_does_not_track_a_real_config(self) -> None:
        gitignore = (SRC.parent / '.gitignore').read_text(encoding='utf-8')
        assert 'src/erp-config.json' in gitignore
        tracked = subprocess.run(['git', 'ls-files', '--', 'src/erp-config.json'], capture_output=True, text=True, cwd=str(SRC.parent), check=False)
        assert tracked.stdout.strip() == ''

    def test_the_example_is_valid_and_not_a_real_server(self) -> None:
        data = json.loads((SRC / 'erp-config.example.json').read_text(encoding='utf-8'))
        assert config.validate_api_url(data['apiBaseUrl']) and config.validate_environment(data['environment'])
        assert 'example' in data['apiBaseUrl']

    def test_no_server_address_is_hardcoded_in_the_erp_package(self) -> None:
        for path in (SRC / 'erp').glob('*.py'):
            text = path.read_text(encoding='utf-8')
            assert 'codecoffee' not in text and 'localhost:10301' not in text or path.name == 'config.py', path.name
        assert 'codecoffee.vn' not in (SRC / 'erp' / 'config.py').read_text(encoding='utf-8')


class TestBuildPipeline:
    @pytest.mark.parametrize('spec', SPECS, ids=lambda p: p.name)
    def test_a_spec_bundles_the_file_and_refuses_to_build_without_it(self, spec:Path) -> None:
        text = spec.read_text(encoding='utf-8')
        assert "erp-config.json', '.')" in text
        assert "os.path.exists('erp-config.json')" in text and 'raise SystemExit' in text
        assert text.index("os.path.exists('erp-config.json')") < text.index("erp-config.json', '.')")   # the check comes first

    @pytest.mark.parametrize('script', BUILD_SCRIPTS, ids=lambda p: p.name)
    def test_a_build_script_writes_the_file_before_pyinstaller(self, script:Path) -> None:
        text = script.read_bytes().decode('utf-8')
        assert 'make-erp-config.py' in text
        assert text.index('make-erp-config.py') < text.index('pyinstaller -')

    def test_the_windows_script_keeps_its_line_endings(self) -> None:
        raw = (SRC / 'build-win3-pi.bat').read_bytes()
        assert raw.count(b'\r\n') == raw.count(b'\n')   # no bare LF crept in
