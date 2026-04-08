# Onboarding Guide

## 1. Mục tiêu

Tài liệu này dành cho người mới vào repo và muốn:
- chuẩn bị môi trường phát triển
- cài dependencies
- chạy Artisan local để debug
- build production package

Repo này là ứng dụng desktop Python/PyQt cho coffee roasting. Entry point chính là [src/artisan.py](../src/artisan.py).

## 2. Yêu cầu môi trường

### Bắt buộc
- `git`
- `Python 3.12+`
- `venv` để tách môi trường cài đặt
- hệ điều hành được hỗ trợ: macOS / Windows / Linux

### Ghi chú theo repo
- `pyproject.toml` yêu cầu `requires-python = '>=3.12'`
- CI hiện chạy với Python `3.14`
- repo có build spec riêng cho từng platform:
  - `src/artisan-mac.spec`
  - `src/artisan-mac_universal.spec`
  - `src/artisan-linux.spec`
  - `src/artisan-win.spec`

## 3. Cần cài những gì

### Runtime dependencies
Cài từ file:
- `src/requirements.txt`

Nhóm dependency chính:
- UI desktop: `PyQt6`, `PyQt6-WebEngine`
- scientific/plotting: `numpy`, `scipy`, `matplotlib`
- device & protocol: `pyserial`, `pymodbus`, `python-snap7`, `Phidget22`, `bleak`, `yoctopuce`, `websockets`
- data/export: `openpyxl`, `lxml`, `protobuf`, `pydantic`, `babel`
- networking: `requests`, `aiohttp`, `aiohttp_jinja2`

### Dev dependencies
Cài thêm từ file:
- `src/requirements-dev.txt`

Nhóm dependency chính:
- test: `pytest`, `pytest-cov`, `pytest-asyncio`, `pytest-mock`, `hypothesis`
- lint/type check: `ruff`, `pylint`, `mypy`, `pyright`, `codespell`
- packaging/dev workflow: `pre-commit`

### Optional system/device setup
Tùy phần cứng bạn dùng, có thể cần cài thêm:
- driver cho serial / Phidget / Yoctopuce / meter / PLC
- trên Linux có thể cần thêm quyền nhóm `dialout` hoặc `uucp`
- trên Linux có thể cần `gnome-keyring` nếu dùng tính năng nhớ password cho artisan.plus

Chi tiết phần cứng xem thêm ở `wiki/Installation.md`.

## 4. Cài dependencies

Thực hiện từ root repo:

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r src/requirements.txt
pip install -r src/requirements-dev.txt
```

### Windows
```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r src\requirements.txt
pip install -r src\requirements-dev.txt
```

## 5. Chạy local để debug

Chạy từ thư mục `src/`:

### macOS / Linux
```bash
cd src
python3 artisan.py
```

### Windows
```powershell
cd src
python artisan.py
```

### Log file
- macOS: `~/Library/Application Support/artisan-scope/Artisan/artisan.log`
- Linux: `~/.local/share/artisan-scope/Artisan/artisan.log`
- Windows: `%localappdata%\artisan-scope\Artisan\artisan.log`

## 6. Lệnh dev thường dùng

Chạy từ `src/` sau khi activate virtualenv:

### Test
```bash
pytest
```

### Coverage
```bash
pytest --cov --cov-report=html
coverage run -m pytest
coverage-badge -o coverage.svg
```

### Lint
```bash
codespell
ruff check .
pylint */*.py
```

### Type check
```bash
mypy
pyright
```

## 7. Build production

### Official production build path
Repo này build **official production packages** bằng **AppVeyor CI/CD**, không phải local build out-of-the-box.

Theo `wiki/HowToBuildArtisan.md`, việc build install package chính thức trên local machine **không làm được trực tiếp nếu không sửa file trong repo**. Vì vậy, nếu mục tiêu là build production package đúng theo flow của dự án, đường đi được support là:
- fork repo lên GitHub
- cấu hình AppVeyor
- push commit để CI build package cho Windows / macOS / Linux

### Local packaging commands (chỉ để thử nghiệm)
Nếu bạn chỉ muốn thử đóng gói local để nghiên cứu hoặc debug packaging, các spec file hiện có là:

#### macOS
Chạy từ `src/`:
```bash
pyinstaller artisan-mac.spec
```

Tuỳ chọn build universal:
```bash
pyinstaller artisan-mac_universal.spec
```

Ghi chú:
- `src/artisan-mac.spec` mặc định dùng Python `3.14` nếu không set env `PYTHON_V`
- spec có thể dùng `QT_PATH` nếu bạn muốn chỉ rõ vị trí Qt
- đây không được repo document là con đường official để ra production package

#### Linux
Chạy từ `src/`:
```bash
pyinstaller artisan-linux.spec
```

Ghi chú:
- đây là local packaging command để thử nghiệm
- official production packages vẫn theo flow AppVeyor/CI

#### Windows
Repo có `src/artisan-win.spec`, nhưng file này ghi rõ là dành cho **Appveyor CI** và phụ thuộc environment variables CI như `APPVEYOR`, `PYTHON_PATH`, `PYQT`, `QT_TRANSL`.

Vì vậy, Windows production build trong repo hiện tại nên xem là:
- build qua CI/Appveyor
- không có local command đơn giản được repo support sẵn

Xem thêm: `wiki/HowToBuildArtisan.md`

## 8. Nơi nên đọc tiếp nếu mới vào repo

- `README.md` — overview sản phẩm
- `wiki/Installation.md` — cài đặt binary + lưu ý device/platform
- `wiki/HowToRunFromSource.md` — chạy từ source
- `src/artisan.py` — process entrypoint
- `src/artisanlib/main.py` — main application shell
- `src/requirements.txt` — runtime dependencies
- `src/requirements-dev.txt` — dev dependencies

## 9. Quick start ngắn nhất

### macOS / Linux
```bash
git clone https://github.com/artisan-roaster-scope/artisan.git
cd artisan
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r src/requirements.txt
pip install -r src/requirements-dev.txt
cd src
python3 artisan.py
```

### Windows
```powershell
git clone https://github.com/artisan-roaster-scope/artisan.git
cd artisan
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r src\requirements.txt
pip install -r src\requirements-dev.txt
cd src
python artisan.py
```
