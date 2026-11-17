"""Tests of make-portable-zip.py: the release zip of the portable Windows build is what erp.updater installs, and its metadata is what the
ERP upload expects (app artisan-windows, SHA-256)."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from erp import updater

SRC = Path(__file__).resolve().parents[3]
SCRIPT = SRC / 'make-portable-zip.py'


@pytest.fixture
def dist(tmp_path:Path) -> Path:
    d = tmp_path / 'dist' / 'artisan'
    (d / '_internal').mkdir(parents=True)
    (d / 'artisan.exe').write_bytes(b'MZ')
    (d / '_internal' / 'python314.dll').write_bytes(b'dll')
    (d / 'erp-config.json').write_text(json.dumps({'apiBaseUrl': 'https://erp.test/api', 'environment': 'production', 'buildCode': 125}),
                                       encoding='utf-8')
    return d


def run(tmp_path:Path, dist:Path, *args:str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), '--dist', str(dist), '--out-dir', str(tmp_path / 'out'), '--version', '4.2.1', *args],
                          capture_output=True, text=True, check=False)


def test_release_zip_and_metadata(tmp_path:Path, dist:Path) -> None:
    r = run(tmp_path, dist, '--build-code', '125')
    assert r.returncode == 0, r.stderr
    release = tmp_path / 'out' / 'artisan-win-4.2.1-125.zip'
    meta = json.loads((tmp_path / 'out' / 'artisan-win-4.2.1-125.json').read_text(encoding='utf-8'))
    assert meta['app'] == 'artisan-windows'
    assert meta['versionCode'] == 125
    assert meta['versionName'] == '4.2.1-125'
    assert meta['apkChecksumAlgorithm'] == 'sha256'
    assert meta['apkChecksum'] == hashlib.sha256(release.read_bytes()).hexdigest()
    assert meta['isForceUpdate'] is False
    with zipfile.ZipFile(release) as z:
        assert sorted(z.namelist()) == ['_internal/python314.dll', 'artisan.exe', 'erp-config.json']


def test_the_updater_installs_what_it_makes(tmp_path:Path, dist:Path) -> None:
    assert run(tmp_path, dist, '--build-code', '125').returncode == 0
    target = tmp_path / 'unpacked'
    app_root = updater.extract(tmp_path / 'out' / 'artisan-win-4.2.1-125.zip', target)
    assert (app_root / 'artisan.exe').read_bytes() == b'MZ'


def test_first_install_zip(tmp_path:Path, dist:Path) -> None:
    launcher = tmp_path / 'Artisan.exe'
    launcher.write_bytes(b'launcher')
    r = run(tmp_path, dist, '--build-code', '125', '--launcher', str(launcher))
    assert r.returncode == 0, r.stderr
    with zipfile.ZipFile(tmp_path / 'out' / 'Artisan-portable-4.2.1-125.zip') as z:
        names = set(z.namelist())
        assert {'Artisan/Artisan.exe', 'Artisan/current.json', 'Artisan/versions/125/artisan.exe'} <= names
        assert json.loads(z.read('Artisan/current.json')) == {'current': '125', 'previous': None, 'blocked': None}


@pytest.mark.parametrize('code', ['', '0', 'abc'])
def test_needs_a_build_number(tmp_path:Path, dist:Path, code:str) -> None:
    assert run(tmp_path, dist, '--build-code', code).returncode == 2


def test_refuses_a_build_that_does_not_know_its_number(tmp_path:Path, dist:Path) -> None:
    # erp-config.json says 125: a zip published as 130 would make the app download itself again and again
    r = run(tmp_path, dist, '--build-code', '130')
    assert r.returncode == 2
    assert 'buildCode' in r.stderr


def test_needs_the_pyinstaller_output(tmp_path:Path) -> None:
    assert run(tmp_path, tmp_path / 'nothing', '--build-code', '125').returncode == 2
