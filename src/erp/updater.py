#!/usr/bin/python
#
# updater.py
#
# ABOUT
# Self-update of the portable Windows build (no Qt here: the dialogs are in update_flow.py).
#
# The roasting PCs run Artisan from a folder laid out by the launcher (launcher/ in this repo, a small Go program):
#
#   <root>\Artisan.exe          the launcher; the shortcuts point here
#   <root>\current.json         {"current": "125", "previous": "120", "blocked": null}: which version folder to start, the one to fall back
#                               to, and a version the launcher had to roll back (it never starts and Artisan does not fetch it again)
#   <root>\versions\125\        one complete PyInstaller onedir build (artisan.exe, erp-config.json, ...) per release
#   <root>\versions\125\.healthy   written by Artisan once it got as far as the login: the launcher only rolls back a version without it
#   <root>\versions\130.partial\   a download in progress, ignored by the launcher
#
# The launcher starts versions\<current>\artisan.exe with LVG_LAUNCHER_ROOT / LVG_LAUNCHER_EXE set. Without them (run from source, an old
# portable folder) the self-update is off. A new release is never written over the running one: it goes into a folder of its own and
# current.json is switched, so a roast in progress is never touched and the switch takes effect at the next start.
#
# The releases are the 'artisan-windows' application of the ERP release list (GET /api/app-version/latest?app=artisan-windows): a zip of
# the onedir folder with its SHA-256, versionCode = the build number of the CircleCI pipeline (config.build_code() of the running build).

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

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from erp import client, config

_log: Final[logging.Logger] = logging.getLogger(__name__)

APP: Final[str] = 'artisan-windows'                 # the application of this build in the ERP release list
ENV_LAUNCHER_ROOT: Final[str] = 'LVG_LAUNCHER_ROOT'  # set by the launcher: the folder that holds current.json and versions\
ENV_LAUNCHER_EXE: Final[str] = 'LVG_LAUNCHER_EXE'    # set by the launcher: its own path, to start it again after a forced update
VERSIONS_DIR: Final[str] = 'versions'
CURRENT_FILE: Final[str] = 'current.json'
HEALTHY_MARK: Final[str] = '.healthy'
PARTIAL_SUFFIX: Final[str] = '.partial'
APP_EXE: Final[str] = 'artisan.exe'
CHECKSUM_ALGORITHM: Final[str] = 'sha256'
MAX_UNPACKED_BYTES: Final[int] = 3 * 1024 ** 3      # a sane bound for a zip bomb; one onedir build is a few hundred MB
DOWNLOAD_TIMEOUT: Final[tuple[float, float]] = (10, 60)
CHUNK: Final[int] = 1024 * 1024


class UpdateError(Exception):
    """An update could not be fetched or installed. `message` is meant for the user."""

    def __init__(self, message:str) -> None:
        super().__init__(message)
        self.message:str = message


class UpdateCancelled(UpdateError):
    def __init__(self) -> None:
        super().__init__('Đã huỷ tải bản cập nhật.')


@dataclass(frozen=True)
class Release:
    version_code:int
    version_name:str
    url:str
    sha256:str
    force:bool
    notes:str

    @property
    def dir_name(self) -> str:
        return str(self.version_code)


# ---- where are we ---------------------------------------------------------------------------------------------------------------

def launcher_root() -> Path|None:
    """The launcher folder if Artisan was started by the launcher, else None (self-update off)."""
    raw = os.environ.get(ENV_LAUNCHER_ROOT)
    if not raw:
        return None
    root = Path(raw)
    return root if (root / VERSIONS_DIR).is_dir() else None


def enabled() -> bool:
    return launcher_root() is not None


def running_dir() -> Path:
    """The folder of the running build (the onedir folder when frozen)."""
    return Path(sys.executable).resolve().parent


def running_version(root:Path) -> str|None:
    """The name of the version folder this build runs from, None if it does not run from <root>\\versions\\."""
    here = running_dir()
    try:
        rel = here.relative_to((root / VERSIONS_DIR).resolve())
    except ValueError:
        return None
    return rel.parts[0] if len(rel.parts) == 1 else None


# ---- the release list of the ERP ----------------------------------------------------------------------------------------------------

def check_download_url(url:Any) -> str:
    """https only (http only to this PC): the file is checked against the SHA-256 of the release list, but that list comes over the same kind of link."""
    try:
        return config.validate_api_url(url)
    except config.ConfigError as e:
        raise UpdateError(f'Địa chỉ tải bản cập nhật không hợp lệ: {url}') from e


def parse_release(data:Any) -> Release|None:
    """The newest release from the "data" of GET /app-version/latest (None: there is none). Raises UpdateError if it is unusable."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise UpdateError('Máy chủ ERP trả thông tin phiên bản không đúng định dạng.')
    code = data.get('versionCode')
    url = data.get('apkUrl')
    checksum = data.get('apkChecksum')
    algorithm = str(data.get('apkChecksumAlgorithm') or '').lower()
    if isinstance(code, bool) or not isinstance(code, int) or code < 1 or not isinstance(url, str) or not url:
        raise UpdateError('Máy chủ ERP trả thông tin phiên bản không đúng định dạng.')
    if algorithm != CHECKSUM_ALGORITHM or not isinstance(checksum, str) or len(checksum) != 64:
        raise UpdateError('Bản cập nhật trên ERP không có checksum SHA-256, không thể kiểm tra nên không cài.')
    return Release(
        version_code=code,
        version_name=str(data.get('versionName') or code),
        url=check_download_url(url),
        sha256=checksum.lower(),
        force=data.get('isForceUpdate') is True,
        notes=str(data.get('releaseNotes') or ''),
    )


def fetch_latest() -> Release|None:
    """The newest Artisan release on the ERP (public endpoint, works before the login). Raises client.ErpAuthError or UpdateError."""
    r = client.public_get(config.get_app_version_latest_url(), {'app': APP})
    return parse_release(client.data_of(r))


def is_newer(release:Release|None, current_code:int|None = None) -> bool:
    """True if `release` is newer than the running build. A build without a build number (0, run from source) never updates itself."""
    current = config.build_code() if current_code is None else current_code
    return release is not None and 0 < current < release.version_code


# ---- current.json ---------------------------------------------------------------------------------------------------------------

def read_current(root:Path) -> dict[str, str|None]:
    """current / previous / blocked of current.json (None for what is missing; an unreadable file reads as empty)."""
    try:
        data = json.loads((root / CURRENT_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    def name(key:str) -> str|None:
        value = data.get(key)
        return value if isinstance(value, str) and value else None
    return {'current': name('current'), 'previous': name('previous'), 'blocked': name('blocked')}


def write_current(root:Path, current:str, previous:str|None, blocked:str|None = None) -> None:
    """Atomic: the launcher never reads half a file, whatever happens to this process."""
    path = root / CURRENT_FILE
    tmp = path.with_name(f'{CURRENT_FILE}.{uuid.uuid4().hex[:8]}.tmp')
    content = json.dumps({'current': current, 'previous': previous, 'blocked': blocked}, indent=2) + '\n'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---- download and install -------------------------------------------------------------------------------------------------------

def is_blocked(root:Path, release:Release) -> bool:
    """True if the launcher rolled `release` back because it did not start: it is not fetched again (a fixed release has a higher number)."""
    return read_current(root)['blocked'] == release.dir_name


def is_staged(root:Path, release:Release) -> bool:
    return (root / VERSIONS_DIR / release.dir_name / APP_EXE).is_file()


def download(release:Release, dest:Path, progress:Callable[[int, int], None]|None = None,
             cancelled:Callable[[], bool]|None = None) -> None:
    """Streams the release zip to `dest` and checks its SHA-256 (the file is removed if it does not match)."""
    try:
        r = client.public_get(release.url, stream=True, timeout=DOWNLOAD_TIMEOUT)
    except client.ErpAuthError as e:
        raise UpdateError('Không kết nối được máy chủ để tải bản cập nhật.') from e
    try:
        if not 200 <= r.status_code < 300:
            raise UpdateError(f'Không tải được bản cập nhật (HTTP {r.status_code}).')
        total = int(r.headers.get('Content-Length') or 0)
        hasher = hashlib.sha256()
        done = 0
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(CHUNK):
                if cancelled is not None and cancelled():
                    raise UpdateCancelled
                if not chunk:
                    continue
                f.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except OSError as e:
        raise UpdateError(f'Không ghi được bản cập nhật xuống đĩa: {e}') from e
    except client.ErpAuthError as e:
        raise UpdateError('Mất kết nối khi tải bản cập nhật.') from e
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    except Exception as e: # pylint: disable=broad-except # requests raises its own errors while streaming
        dest.unlink(missing_ok=True)
        raise UpdateError(f'Mất kết nối khi tải bản cập nhật: {type(e).__name__}') from e
    finally:
        r.close()
    if hasher.hexdigest() != release.sha256:
        dest.unlink(missing_ok=True)
        raise UpdateError('File cập nhật tải về bị hỏng (SHA-256 không khớp). Hãy thử lại.')


def _safe_member(name:str) -> PurePosixPath:
    path = PurePosixPath(name.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or (path.parts and ':' in path.parts[0]):
        raise UpdateError(f'File cập nhật chứa đường dẫn không hợp lệ: {name}')
    return path


def extract(zip_path:Path, target:Path) -> Path:
    """Unpacks the release zip into `target` (refusing paths that leave it) and returns the folder that holds artisan.exe: the zip root, or its
    only top folder (a zip of dist\\artisan\\ itself)."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = z.infolist()
            if sum(m.file_size for m in members) > MAX_UNPACKED_BYTES:
                raise UpdateError('File cập nhật quá lớn sau khi giải nén.')
            for member in members:
                _safe_member(member.filename)
            z.extractall(target)
    except zipfile.BadZipFile as e:
        raise UpdateError('File cập nhật không phải file zip hợp lệ.') from e
    except OSError as e:
        raise UpdateError(f'Không giải nén được bản cập nhật: {e}') from e
    if (target / APP_EXE).is_file():
        return target
    entries = [p for p in target.iterdir() if not p.name.startswith('__MACOSX')]
    if len(entries) == 1 and entries[0].is_dir() and (entries[0] / APP_EXE).is_file():
        return entries[0]
    raise UpdateError(f'File cập nhật không chứa {APP_EXE}.')


_busy:Final[threading.Lock] = threading.Lock()


def stage(root:Path, release:Release, progress:Callable[[int, int], None]|None = None,
          cancelled:Callable[[], bool]|None = None) -> Path:
    """Downloads, checks and unpacks `release` into <root>\\versions\\<versionCode>\\ (nothing to do if it is already there). Only one at a time:
    a second call while one runs raises UpdateError."""
    if not _busy.acquire(blocking=False): # pylint: disable=consider-using-with # released in the finally below
        raise UpdateError('Đang tải bản cập nhật, vui lòng chờ.')
    try:
        versions = root / VERSIONS_DIR
        final = versions / release.dir_name
        if is_staged(root, release):
            return final
        partial = versions / (release.dir_name + PARTIAL_SUFFIX)
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        try:
            package = partial / 'package.zip'
            download(release, package, progress, cancelled)
            app_root = extract(package, partial / 'unpacked')
            if final.exists():
                shutil.rmtree(final) # a folder without artisan.exe: what is left of an earlier failed attempt
            os.replace(app_root, final)
        finally:
            shutil.rmtree(partial, ignore_errors=True)
        _log.info('update %s staged in %s', release.version_name, final)
        return final
    finally:
        _busy.release()


def activate(root:Path, release:Release) -> None:
    """Makes the launcher start `release` next time; the version running now becomes the one to fall back to."""
    state = read_current(root)
    if state['current'] == release.dir_name or state['blocked'] == release.dir_name:
        return
    previous = running_version(root) or state['current']
    if previous == release.dir_name:
        previous = state['previous']
    write_current(root, release.dir_name, previous, state['blocked'])
    _log.info('update %s activated (fallback %s)', release.version_name, previous)


# ---- housekeeping ---------------------------------------------------------------------------------------------------------------

def mark_healthy() -> None:
    """Tells the launcher this version starts fine (got as far as the login), so it is never rolled back. Never raises."""
    root = launcher_root()
    if root is None:
        return
    name = running_version(root)
    if name is None:
        return
    try:
        (root / VERSIONS_DIR / name / HEALTHY_MARK).touch()
    except OSError as e:
        _log.warning('could not mark %s healthy: %s', name, e)


def cleanup(root:Path) -> list[str]:
    """Removes every version folder except the current, the fallback and the running one (and leftovers of interrupted downloads).
    Returns the names removed. Never raises."""
    if _busy.locked():
        return []
    state = read_current(root)
    keep = {state['current'], state['previous'], running_version(root)} - {None}
    removed:list[str] = []
    try:
        entries = list((root / VERSIONS_DIR).iterdir())
    except OSError:
        return removed
    for entry in entries:
        if not entry.is_dir() or entry.name in keep:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(entry.name)
    if removed:
        _log.info('removed old versions %s', removed)
    return removed


def restart_via_launcher() -> bool:
    """Starts the launcher again, which waits for this process to end and then starts the current version. False if there is no launcher."""
    root = launcher_root()
    exe = os.environ.get(ENV_LAUNCHER_EXE)
    if root is None or not exe or not Path(exe).is_file():
        return False
    env = {k: v for k, v in os.environ.items() if k not in (ENV_LAUNCHER_ROOT, ENV_LAUNCHER_EXE)}
    flags = 0
    if sys.platform == 'win32':
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe, '--wait-pid', str(os.getpid())], cwd=str(root), env=env, close_fds=True, # pylint: disable=consider-using-with
                     creationflags=flags)
    return True
