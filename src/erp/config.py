#!/usr/bin/python
#
# config.py
#
# ABOUT
# Configuration of the connection to the LVG ERP (backend-go, API prefix /api)

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

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

# keyring service name under which the ERP refresh token is stored; the environment is appended (see keyring_service())
app_name: Final[str] = 'lvg-erp'

# when True the artisan.plus cloud features are switched off (the ERP replaces them)
ERP_ONLY: Final[bool] = True

# (connect, read) timeout in seconds of requests to the ERP
timeout: Final[tuple[float, float]] = (5, 15)

# (connect, read) timeout in seconds of a file upload (a roast report of a few MB over a poor connection)
upload_timeout: Final[tuple[float, float]] = (5, 60)

# client side validation, mirrors the rules of the ERP login endpoint
min_username_len: Final[int] = 3
max_username_len: Final[int] = 30
min_passwd_len: Final[int] = 6

# how often the roaster list is refreshed while the selection dialog is open (the realtime push only makes it faster)
roaster_poll_seconds: Final[int] = 15
# a lease that could not be renewed for this share of its TTL is shown as "suspect" (the ERP may hand the roaster to someone else soon)
lease_suspect_fraction: Final[float] = 0.8

# ---- which ERP this build talks to ---------------------------------------------------------------------------------------------
#
# There is no default server: every build carries the address of the environment it was made for (erp-config.json, written by
# make-erp-config.py at build time and bundled by the PyInstaller specs). The environment variables below win over the file and are meant
# for development, tests and the end-to-end runs. If neither is there Artisan refuses to log in instead of guessing a server.

ENV_API_URL: Final[str] = 'ERP_API_BASE_URL'
ENV_ENVIRONMENT: Final[str] = 'ERP_ENVIRONMENT'
ENV_SOCKET_URL: Final[str] = 'ERP_SOCKET_URL'
CONFIG_FILENAME: Final[str] = 'erp-config.json'
PRODUCTION_NAMES: Final[frozenset[str]] = frozenset({'production', 'prod'})
_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({'localhost', '127.0.0.1', '::1'})
_ENVIRONMENT_RE: Final[re.Pattern[str]] = re.compile(r'^[a-z0-9][a-z0-9-]{0,19}$')


class ConfigError(Exception):
    """The ERP server of this build is missing or invalid. `message` is meant for the user."""

    def __init__(self, message:str) -> None:
        super().__init__(message)
        self.message:str = message


@dataclass(frozen=True)
class ServerConfig:
    api_base_url:str          # e.g. https://erp-api.example.vn/api (no trailing slash)
    environment:str           # e.g. production, staging, dev: keeps the data of one environment apart from another
    socket_url:str|None       # None: derived from the API URL


def validate_api_url(value:Any) -> str:
    """The API base URL without trailing slash. https only, except for this PC (localhost): the password must not travel in the clear."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError('Địa chỉ máy chủ ERP (apiBaseUrl) bị trống.')
    url = value.strip().rstrip('/')
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in ('http', 'https') or not host or parsed.query or parsed.fragment:
        raise ConfigError(f'Địa chỉ máy chủ ERP không hợp lệ: {url}')
    if parsed.scheme == 'http' and host not in _LOCAL_HOSTS:
        raise ConfigError(f'Địa chỉ máy chủ ERP phải dùng https (http chỉ được dùng với localhost): {url}')
    return url


def validate_environment(value:Any) -> str:
    if not isinstance(value, str) or not _ENVIRONMENT_RE.match(value.strip().lower()):
        raise ConfigError('Tên môi trường (environment) chỉ gồm chữ thường, số và dấu gạch ngang, tối đa 20 ký tự.')
    return value.strip().lower()


def environment_from_host(url:str) -> str:
    """'localhost' for http://localhost:10301/api, 'erp-api-example-vn' for https://erp-api.example.vn/api: a name for development runs."""
    host = (urlparse(url).hostname or 'unknown').lower()
    name = re.sub(r'[^a-z0-9]+', '-', host).strip('-')[:20].strip('-')
    return name or 'unknown'


def _config_file() -> Path:
    """erp-config.json: inside the PyInstaller bundle when frozen, next to the source (src/) otherwise."""
    meipass = getattr(sys, '_MEIPASS', None)
    if getattr(sys, 'frozen', False) and meipass:
        return Path(meipass) / CONFIG_FILENAME
    return Path(__file__).resolve().parent.parent / CONFIG_FILENAME


def _read_file() -> dict[str, Any]|None:
    path = _config_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ConfigError(f'Không đọc được file cấu hình {CONFIG_FILENAME}: {e}') from e
    if not isinstance(data, dict):
        raise ConfigError(f'File cấu hình {CONFIG_FILENAME} không đúng định dạng.')
    return data


_cache:dict[tuple[str|None, str|None, str|None], ServerConfig] = {}


def reset_cache() -> None:
    """For tests (and anything that changes the environment variables or the file while running)."""
    _cache.clear()


def server() -> ServerConfig:
    """The ERP of this build. Raises ConfigError if there is none or it is invalid."""
    key = (os.environ.get(ENV_API_URL), os.environ.get(ENV_ENVIRONMENT), os.environ.get(ENV_SOCKET_URL))
    cached = _cache.get(key)
    if cached is not None:
        return cached
    env_url, env_env, env_socket = key
    if env_url and env_url.strip():
        api = validate_api_url(env_url)
        environment = validate_environment(env_env) if env_env and env_env.strip() else environment_from_host(api)
        socket = validate_api_url(env_socket) if env_socket and env_socket.strip() else None
    else:
        data = _read_file()
        if data is None:
            raise ConfigError(
                'Bản Artisan này chưa được cấu hình máy chủ ERP.\n'
                'Hãy cài bản build dành cho môi trường của bạn. Khi chạy từ mã nguồn (dev), đặt biến môi trường '
                f'{ENV_API_URL}, ví dụ {ENV_API_URL}=http://localhost:10301/api')
        api = validate_api_url(data.get('apiBaseUrl'))
        if not data.get('environment'):
            raise ConfigError(f'File cấu hình {CONFIG_FILENAME} thiếu "environment" (production, staging, ...).')
        environment = validate_environment(data.get('environment'))
        socket = validate_api_url(data['socketUrl']) if data.get('socketUrl') else None
    result = ServerConfig(api, environment, socket)
    _cache[key] = result
    return result


def check() -> None:
    """Raises ConfigError if this build has no usable ERP server."""
    server()


def environment() -> str:
    return server().environment


def is_production() -> bool:
    return environment() in PRODUCTION_NAMES


def environment_label() -> str:
    """'' for production, else the upper case name ('STAGING'): shown in the title bar and on the login so that nobody roasts on a test server by mistake."""
    try:
        env = environment()
    except ConfigError:
        return ''
    return '' if env in PRODUCTION_NAMES else env.upper()


def keyring_service() -> str:
    """Keychain service of the refresh token: one per environment, so that two builds on one PC never share or overwrite a login."""
    return f'{app_name}:{environment()}'


def get_api_base_url() -> str:
    return server().api_base_url


def get_login_url() -> str:
    return get_api_base_url() + '/auth/login'


def get_refresh_url() -> str:
    return get_api_base_url() + '/auth/refresh'


def get_logout_url() -> str:
    """Artisan's own logout. Deliberately NOT /auth/logout: that one revokes the refresh tokens of every device of the user."""
    return get_api_base_url() + '/artisan/logout'


def get_roasters_url() -> str:
    return get_api_base_url() + '/roasters'


def get_artisan_url() -> str:
    """Prefix of the roasting API made for Artisan (find commands, start / finish / cancel a roast batch)."""
    return get_api_base_url() + '/artisan'


def get_socket_url() -> str:
    """Origin of the Socket.IO gateway: configured explicitly, else the API URL without its /api prefix (as the ERP mobile app derives it)."""
    configured = server().socket_url
    if configured:
        return configured
    base = get_api_base_url()
    return base[:-4] if base.endswith('/api') else base
