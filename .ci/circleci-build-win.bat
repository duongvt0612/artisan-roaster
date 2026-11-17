@echo off
:: ABOUT
:: CircleCI build of the portable Windows build of Artisan (LVG ERP fork), see .circleci/config.yml.
:: Expects Python in %PYTHON_PATH% (installed by the job), the launcher in ws\Artisan.exe (workspace of the launcher job), and in the
:: environment: ERP_API_BASE_URL, ERP_ENVIRONMENT (+ optional ERP_SOCKET_URL) from the CircleCI context, ERP_BUILD_CODE (pipeline number).
:: Output: src\dist-portable\ (release zip + metadata json + first-install zip).
::
:: LICENSE
:: This program or module is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License
:: as published by the Free Software Foundation, either version 2 of the License, or version 3 of the License, or (at your option) any
:: later version. It is provided for educational purposes and is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY.
setlocal enabledelayedexpansion
if /i "%CIRCLECI%" NEQ "true" (
    echo This file is for use on CircleCI only.
    exit /b 1
)
if not defined PYTHON_PATH (echo PYTHON_PATH is not set & exit /b 1)
if not defined ERP_BUILD_CODE (echo ERP_BUILD_CODE is not set & exit /b 1)
if not exist ws\Artisan.exe (echo launcher ws\Artisan.exe is missing & exit /b 1)

set PATH=%PYTHON_PATH%;%PYTHON_PATH%\Scripts;%PATH%
set QT_TRANSL=%PYTHON_PATH%\Lib\site-packages\PyQt6\Qt6\translations
if not defined PYQT set PYQT=6
if not defined PYUIC set PYUIC=pyuic6.exe
:: the Windows pyinstaller version is pinned (as a comment) in requirements.txt, as on Appveyor
for /f "usebackq delims=" %%a in (`python -c "import re;print(re.search(r'pyinstaller==([0-9.]+).*platform_system=.Windows.', open('src/requirements.txt').read()).group(1))"`) do set PYINSTALLER_VER=%%a
if not defined PYINSTALLER_VER (echo pyinstaller version not found in src\requirements.txt & exit /b 1)
echo PYINSTALLER_VER=%PYINSTALLER_VER%

call .ci\install-win.bat
if ERRORLEVEL 1 (echo ** Failed in install-win.bat & exit /b 1)

cd src
:: copyright year of the exe properties
powershell -NoProfile -Command "(Get-Content version-metadata.yml) -replace 'yyyy', (Get-Date).Year | Set-Content -Encoding ascii version-metadata.yml"
:: the git revision shown in the About box
for /f "usebackq delims=" %%a in (`git rev-parse --verify --short HEAD`) do set GIT_VERSION=%%a
powershell -NoProfile -Command "(Get-Content artisanlib\__init__.py) -replace \"__revision__ = '.*'\", \"__revision__ = '%GIT_VERSION%'\" | Set-Content -Encoding ascii artisanlib\__init__.py"

set ARTISAN_PORTABLE=1
set ARTISAN_LAUNCHER=%CD%\..\ws\Artisan.exe
call build-win3-pi.bat
if ERRORLEVEL 1 (echo ** Failed in build-win3-pi.bat & exit /b 1)
dir dist-portable
exit /b 0
