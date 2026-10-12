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

import os
from typing import Final

# keyring service name under which the ERP refresh token is stored
app_name: Final[str] = 'lvg-erp'

# when True the artisan.plus cloud features are switched off (the ERP replaces them)
ERP_ONLY: Final[bool] = True

# (connect, read) timeout in seconds of requests to the ERP
timeout: Final[tuple[float, float]] = (5, 15)

# client side validation, mirrors the rules of the ERP login endpoint
min_username_len: Final[int] = 3
max_username_len: Final[int] = 30
min_passwd_len: Final[int] = 6

default_api_base_url: Final[str] = 'https://erp-api.codecoffee.vn/api'


def get_api_base_url() -> str:
    return os.environ.get('ERP_API_BASE_URL', default_api_base_url).rstrip('/')


def get_login_url() -> str:
    return get_api_base_url() + '/auth/login'


def get_refresh_url() -> str:
    return get_api_base_url() + '/auth/refresh'


def get_logout_url() -> str:
    return get_api_base_url() + '/auth/logout'
