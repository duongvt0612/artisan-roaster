#!/usr/bin/env python3
#
# make-portable-zip.py
#
# ABOUT
# Packs the PyInstaller onedir build (dist/artisan/) of the portable Windows build for the self-update (see erp/updater.py):
#
#   artisan-win-<version>-<build>.zip        the release: the content of dist/artisan/ (artisan.exe at the root of the zip);
#                                            uploaded to the ERP release list as application 'artisan-windows'
#   artisan-win-<version>-<build>.json       its metadata: app, versionCode, versionName, apkChecksum (SHA-256), ... The ERP admin page
#                                            fills its upload form from it; CI reads it to upload
#   Artisan-portable-<version>-<build>.zip   (with --launcher) the first install of a roasting PC: the launcher, current.json and
#                                            versions/<build>/; unzip to e.g. C:\LVG\Artisan and point the shortcut at Artisan.exe
#
# The build number (versionCode) is the one written into erp-config.json by make-erp-config.py (ERP_BUILD_CODE).

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

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = 'artisan-windows'
APP_EXE = 'artisan.exe'
LAUNCHER_EXE = 'Artisan.exe'


def read_version() -> str:
    text = (HERE / 'artisanlib' / '__init__.py').read_text(encoding='utf-8')
    match = re.search(r"^__version__\s*=\s*'([^']+)'", text, re.MULTILINE)
    if not match:
        raise SystemExit('make-portable-zip: __version__ not found in artisanlib/__init__.py')
    return match.group(1)


def sha256_of(path:Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def add_tree(z:zipfile.ZipFile, source:Path, prefix:str) -> None:
    """Adds every file below `source` as <prefix><relative path> (forward slashes, sorted: the same build gives the same zip listing)."""
    for path in sorted(p for p in source.rglob('*') if p.is_file()):
        z.write(path, prefix + path.relative_to(source).as_posix())


def main(argv:list[str]|None = None) -> int:
    parser = argparse.ArgumentParser(description='Pack the portable Windows build of Artisan for the self-update.')
    parser.add_argument('--dist', default=str(HERE / 'dist' / 'artisan'), help='the PyInstaller onedir folder')
    parser.add_argument('--out-dir', default=str(HERE / 'dist-portable'), help='where the zip files go')
    parser.add_argument('--build-code', default=os.environ.get('ERP_BUILD_CODE'), help='build number = versionCode on the ERP')
    parser.add_argument('--version', default=None, help='Artisan version (default: artisanlib.__version__)')
    parser.add_argument('--launcher', default=None, help='Artisan.exe launcher: also make the first-install zip')
    parser.add_argument('--release-notes', default=os.environ.get('RELEASE_NOTES', ''), help='release notes for the ERP (optional)')
    args = parser.parse_args(argv)

    dist = Path(args.dist)
    if not (dist / APP_EXE).is_file():
        print(f'make-portable-zip: {dist / APP_EXE} not found: run PyInstaller first', file=sys.stderr)
        return 2
    raw = str(args.build_code or '').strip()
    if not raw.isdigit() or int(raw) < 1:
        print('make-portable-zip: a positive --build-code (or ERP_BUILD_CODE) is needed', file=sys.stderr)
        return 2
    build = int(raw)
    config = dist / 'erp-config.json'
    if config.is_file():
        bundled = json.loads(config.read_text(encoding='utf-8')).get('buildCode')
        if bundled != build:
            print(f'make-portable-zip: erp-config.json of the build has buildCode {bundled}, not {build}: the app would not know its version',
                  file=sys.stderr)
            return 2
    version = args.version or read_version()
    name = f'{version}-{build}'
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    release = out / f'artisan-win-{name}.zip'
    with zipfile.ZipFile(release, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        add_tree(z, dist, '')
    checksum = sha256_of(release)
    metadata = {
        'app': APP,
        'versionCode': build,
        'versionName': name,
        'apkChecksum': checksum,
        'apkChecksumAlgorithm': 'sha256',
        'isForceUpdate': False,
        'releaseNotes': args.release_notes,
        'size': release.stat().st_size,
    }
    (out / f'artisan-win-{name}.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'make-portable-zip: {release} ({metadata["size"]} bytes, sha256 {checksum})')

    if args.launcher:
        launcher = Path(args.launcher)
        if not launcher.is_file():
            print(f'make-portable-zip: launcher {launcher} not found', file=sys.stderr)
            return 2
        first = out / f'Artisan-portable-{name}.zip'
        with zipfile.ZipFile(first, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(launcher, f'Artisan/{LAUNCHER_EXE}')
            z.writestr('Artisan/current.json', json.dumps({'current': str(build), 'previous': None, 'blocked': None}, indent=2) + '\n')
            add_tree(z, dist, f'Artisan/versions/{build}/')
        print(f'make-portable-zip: {first} (first install)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
