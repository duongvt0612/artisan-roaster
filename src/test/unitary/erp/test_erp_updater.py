"""Tests of the self-update of the portable build (erp.updater): release list parsing, download + SHA-256 check, safe unpacking, current.json
and the cleanup of old versions. No network and no launcher: the ERP calls and the running folder are replaced."""

import hashlib
import io
import json
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from erp import client, config, updater


def make_zip(files:dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


GOOD_ZIP = make_zip({'artisan.exe': b'MZ-exe', 'erp-config.json': b'{}', '_internal/lib.dll': b'dll'})


def release_for(content:bytes, code:int = 125, force:bool = False, url:str = 'https://releases.example.vn/artisan-windows/125/artisan-125.zip') -> updater.Release:
    return updater.Release(version_code=code, version_name=f'4.2.1-{code}', url=url,
                           sha256=hashlib.sha256(content).hexdigest(), force=force, notes='')


class FakeResponse:
    def __init__(self, content:bytes, status:int = 200, chunk:int = 7) -> None:
        self.status_code = status
        self.headers = {'Content-Length': str(len(content))}
        self._content = content
        self._chunk = chunk
        self.closed = False

    def iter_content(self, _size:int) -> Iterator[bytes]:
        for i in range(0, len(self._content), self._chunk):
            yield self._content[i:i + self._chunk]

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def root(tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> Path:
    """A launcher folder with one version (120) that is the running one."""
    r = tmp_path / 'Artisan'
    (r / 'versions' / '120').mkdir(parents=True)
    (r / 'versions' / '120' / 'artisan.exe').write_bytes(b'old')
    (r / 'current.json').write_text(json.dumps({'current': '120', 'previous': None}), encoding='utf-8')
    monkeypatch.setenv(updater.ENV_LAUNCHER_ROOT, str(r))
    running = (r / 'versions' / '120').resolve()
    monkeypatch.setattr(updater, 'running_dir', lambda: running)
    return r


def serve(monkeypatch:pytest.MonkeyPatch, content:bytes, status:int = 200) -> list[str]:
    calls:list[str] = []

    def fake_get(url:str, params:Any = None, *, stream:bool = False, timeout:Any = None) -> FakeResponse: # noqa: ARG001
        calls.append(url)
        return FakeResponse(content, status)

    monkeypatch.setattr(client, 'public_get', fake_get)
    return calls


# ---- build number ---------------------------------------------------------------------------------------------------------------

class TestBuildCode:
    def test_from_the_environment(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_BUILD_CODE, '125')
        assert config.build_code() == 125

    def test_from_the_bundled_file(self, monkeypatch:pytest.MonkeyPatch, tmp_path:Path) -> None:
        monkeypatch.delenv(config.ENV_BUILD_CODE, raising=False)
        cfg = tmp_path / 'erp-config.json'
        cfg.write_text(json.dumps({'apiBaseUrl': 'https://erp.test/api', 'environment': 'production', 'buildCode': 77}), encoding='utf-8')
        monkeypatch.setattr(config, '_config_file', lambda: cfg)
        assert config.build_code() == 77

    def test_unknown_is_zero(self, monkeypatch:pytest.MonkeyPatch, tmp_path:Path) -> None:
        monkeypatch.delenv(config.ENV_BUILD_CODE, raising=False)
        monkeypatch.setattr(config, '_config_file', lambda: tmp_path / 'missing.json')
        assert config.build_code() == 0
        monkeypatch.setenv(config.ENV_BUILD_CODE, 'abc')
        assert config.build_code() == 0


# ---- release list ---------------------------------------------------------------------------------------------------------------

LATEST = {'versionCode': 125, 'versionName': '4.2.1-125', 'apkUrl': 'https://releases.example.vn/a.zip',
          'apkChecksum': 'A' * 64, 'apkChecksumAlgorithm': 'sha256', 'isForceUpdate': True, 'releaseNotes': None}


class TestParseRelease:
    def test_none_means_no_release(self) -> None:
        assert updater.parse_release(None) is None

    def test_reads_the_reply_of_the_erp(self) -> None:
        r = updater.parse_release(LATEST)
        assert r == updater.Release(125, '4.2.1-125', 'https://releases.example.vn/a.zip', 'a' * 64, True, '')
        assert r is not None and r.dir_name == '125'

    @pytest.mark.parametrize('change', [
        {'apkChecksumAlgorithm': 'md5'},          # Artisan only trusts SHA-256
        {'apkChecksum': None},
        {'versionCode': 0},
        {'versionCode': '125'},
        {'apkUrl': 'http://releases.example.vn/a.zip'}, # no plain http except to this PC
    ])
    def test_refuses_what_it_cannot_check(self, change:dict[str, Any]) -> None:
        with pytest.raises(updater.UpdateError):
            updater.parse_release({**LATEST, **change})

    @pytest.mark.parametrize('body', [None, 'not json', {'html': 'login page'}])
    def test_a_200_that_is_not_the_erp_reply_is_an_error(self, monkeypatch:pytest.MonkeyPatch, body:Any) -> None:
        # e.g. the login page of a captive portal: must not read as "no release" (a forced update would be skipped)
        class Reply:
            status_code = 200

            @staticmethod
            def json() -> Any:
                if body == 'not json':
                    raise ValueError
                return body

        monkeypatch.setattr(client, 'public_get', lambda *_a, **_k: Reply())
        with pytest.raises(client.ErpAuthError):
            updater.fetch_latest()

    def test_data_null_means_no_release(self, monkeypatch:pytest.MonkeyPatch) -> None:
        class Reply:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {'success': True, 'errors': [], 'errorMessages': [], 'data': None} # what the ERP sends

        monkeypatch.setattr(client, 'public_get', lambda *_a, **_k: Reply())
        assert updater.fetch_latest() is None

    def test_fetch_asks_for_the_artisan_application(self, monkeypatch:pytest.MonkeyPatch) -> None:
        seen:dict[str, Any] = {}

        class Reply:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {'success': True, 'data': LATEST}

        def fake_get(url:str, params:Any = None, **_:Any) -> Any:
            seen['url'], seen['params'] = url, params
            return Reply()

        monkeypatch.setattr(client, 'public_get', fake_get)
        assert updater.fetch_latest() is not None
        assert seen == {'url': 'https://erp.test/api/app-version/latest', 'params': {'app': 'artisan-windows'}}


class TestIsNewer:
    def test_compares_with_the_build_number(self) -> None:
        r = release_for(GOOD_ZIP, code=125)
        assert updater.is_newer(r, 120)
        assert not updater.is_newer(r, 125)
        assert not updater.is_newer(r, 130)

    def test_a_build_without_number_never_updates(self) -> None:
        assert not updater.is_newer(release_for(GOOD_ZIP), 0)
        assert not updater.is_newer(None, 120)


# ---- launcher ---------------------------------------------------------------------------------------------------------------------

class TestLauncher:
    def test_off_without_the_launcher(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(updater.ENV_LAUNCHER_ROOT, raising=False)
        assert updater.launcher_root() is None
        assert not updater.enabled()

    def test_off_if_the_folder_is_not_a_launcher_folder(self, monkeypatch:pytest.MonkeyPatch, tmp_path:Path) -> None:
        monkeypatch.setenv(updater.ENV_LAUNCHER_ROOT, str(tmp_path))
        assert updater.launcher_root() is None

    def test_on_with_the_launcher(self, root:Path) -> None:
        assert updater.launcher_root() == root
        assert updater.running_version(root) == '120'

    def test_restart_needs_the_launcher_exe(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(updater.ENV_LAUNCHER_EXE, raising=False)
        assert updater.launcher_root() == root
        assert not updater.restart_via_launcher()


# ---- download, unpack, activate --------------------------------------------------------------------------------------------------

class TestStage:
    def test_downloads_checks_and_unpacks(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        serve(monkeypatch, GOOD_ZIP)
        progress:list[tuple[int, int]] = []
        final = updater.stage(root, release_for(GOOD_ZIP), lambda d, t: progress.append((d, t)))
        assert final == root / 'versions' / '125'
        assert (final / 'artisan.exe').read_bytes() == b'MZ-exe'
        assert (final / '_internal' / 'lib.dll').is_file()
        assert not (root / 'versions' / '125.partial').exists()
        assert progress[-1] == (len(GOOD_ZIP), len(GOOD_ZIP))

    def test_a_zip_of_the_onedir_folder_itself(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        content = make_zip({'artisan/artisan.exe': b'MZ', 'artisan/_internal/x.dll': b'x'})
        serve(monkeypatch, content)
        final = updater.stage(root, release_for(content))
        assert (final / 'artisan.exe').read_bytes() == b'MZ'

    def test_wrong_checksum_installs_nothing(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        serve(monkeypatch, GOOD_ZIP)
        bad = release_for(b'something else')
        with pytest.raises(updater.UpdateError, match='SHA-256'):
            updater.stage(root, bad)
        assert sorted(p.name for p in (root / 'versions').iterdir()) == ['120']

    def test_http_error_installs_nothing(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        serve(monkeypatch, b'not found', status=404)
        with pytest.raises(updater.UpdateError, match='404'):
            updater.stage(root, release_for(GOOD_ZIP))
        assert sorted(p.name for p in (root / 'versions').iterdir()) == ['120']

    @pytest.mark.parametrize('name', ['../evil.exe', '/abs/evil.exe', 'C:/evil.exe', 'a/../../evil.exe'])
    def test_refuses_paths_that_leave_the_folder(self, root:Path, monkeypatch:pytest.MonkeyPatch, name:str) -> None:
        content = make_zip({'artisan.exe': b'MZ', name: b'x'})
        serve(monkeypatch, content)
        with pytest.raises(updater.UpdateError, match='không hợp lệ'):
            updater.stage(root, release_for(content))
        assert not (root.parent / 'evil.exe').exists()
        assert sorted(p.name for p in (root / 'versions').iterdir()) == ['120']

    def test_refuses_a_zip_without_artisan(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        content = make_zip({'readme.txt': b'x'})
        serve(monkeypatch, content)
        with pytest.raises(updater.UpdateError, match='artisan.exe'):
            updater.stage(root, release_for(content))

    def test_cancel(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        serve(monkeypatch, GOOD_ZIP)
        with pytest.raises(updater.UpdateCancelled):
            updater.stage(root, release_for(GOOD_ZIP), cancelled=lambda: True)
        assert not updater.is_staged(root, release_for(GOOD_ZIP))

    def test_nothing_to_do_if_already_there(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        calls = serve(monkeypatch, GOOD_ZIP)
        updater.stage(root, release_for(GOOD_ZIP))
        updater.stage(root, release_for(GOOD_ZIP))
        assert len(calls) == 1


class TestActivate:
    def test_running_version_becomes_the_fallback(self, root:Path) -> None:
        updater.activate(root, release_for(GOOD_ZIP, code=125))
        assert updater.read_current(root) == {'current': '125', 'previous': '120', 'blocked': None}

    def test_two_downloads_before_a_restart_keep_the_running_one_as_fallback(self, root:Path) -> None:
        updater.activate(root, release_for(GOOD_ZIP, code=125))
        updater.activate(root, release_for(GOOD_ZIP, code=126))
        assert updater.read_current(root) == {'current': '126', 'previous': '120', 'blocked': None}

    def test_a_release_the_launcher_rolled_back_is_not_activated_again(self, root:Path) -> None:
        updater.write_current(root, '120', None, blocked='125')
        assert updater.is_blocked(root, release_for(GOOD_ZIP, code=125))
        updater.activate(root, release_for(GOOD_ZIP, code=125))
        assert updater.read_current(root) == {'current': '120', 'previous': None, 'blocked': '125'}
        updater.activate(root, release_for(GOOD_ZIP, code=126)) # the fixed release goes through and keeps the block
        assert updater.read_current(root) == {'current': '126', 'previous': '120', 'blocked': '125'}

    def test_an_older_download_never_replaces_a_newer_active_one(self, root:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        content = GOOD_ZIP
        serve(monkeypatch, content)
        updater.stage(root, release_for(content, code=126))
        updater.activate(root, release_for(content, code=126))
        updater.stage(root, release_for(content, code=125))
        updater.activate(root, release_for(content, code=125)) # finished last, but older
        assert updater.read_current(root)['current'] == '126'

    def test_write_is_atomic_and_leaves_no_temp_file(self, root:Path) -> None:
        updater.write_current(root, '125', '120')
        assert sorted(os.listdir(root)) == ['current.json', 'versions']

    def test_a_broken_current_file_reads_as_empty(self, root:Path) -> None:
        (root / 'current.json').write_text('{oops', encoding='utf-8')
        assert updater.read_current(root) == {'current': None, 'previous': None, 'blocked': None}


class TestHousekeeping:
    def test_mark_healthy_marks_the_running_version(self, root:Path) -> None:
        updater.mark_healthy()
        assert (root / 'versions' / '120' / '.healthy').is_file()

    def test_cleanup_keeps_current_fallback_and_running(self, root:Path) -> None:
        for name in ('100', '110', '125', '126.partial'):
            (root / 'versions' / name).mkdir()
        updater.write_current(root, '125', '110')
        removed = updater.cleanup(root)
        assert sorted(removed) == ['100', '126.partial']
        assert sorted(p.name for p in (root / 'versions').iterdir()) == ['110', '120', '125']

    def test_cleanup_keeps_a_newer_staged_version_not_yet_activated(self, root:Path) -> None:
        (root / 'versions' / '130').mkdir()
        (root / 'versions' / '130' / 'artisan.exe').write_bytes(b'MZ')
        (root / 'versions' / '131').mkdir() # newer but incomplete: a leftover
        assert updater.cleanup(root) == ['131']
        assert (root / 'versions' / '130' / 'artisan.exe').is_file()

    def test_cleanup_does_nothing_while_a_download_runs(self, root:Path) -> None:
        (root / 'versions' / '126.partial').mkdir()
        with updater._busy: # noqa: SLF001
            assert updater.cleanup(root) == []
        assert (root / 'versions' / '126.partial').is_dir()
