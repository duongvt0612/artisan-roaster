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

default_api_base_url: Final[str] = 'https://erp-api.codecoffee.vn/api'


def get_api_base_url() -> str:
    return os.environ.get('ERP_API_BASE_URL', default_api_base_url).rstrip('/')


def get_login_url() -> str:
    return get_api_base_url() + '/auth/login'


def get_refresh_url() -> str:
    return get_api_base_url() + '/auth/refresh'


def get_logout_url() -> str:
    return get_api_base_url() + '/auth/logout'


def get_roasters_url() -> str:
    return get_api_base_url() + '/roasters'


def get_artisan_url() -> str:
    """Prefix of the roasting API made for Artisan (find commands, start / finish / cancel a roast batch)."""
    return get_api_base_url() + '/artisan'


def get_socket_url() -> str:
    """Origin of the Socket.IO gateway: the API URL without its /api prefix (as the ERP mobile app derives it)."""
    base = get_api_base_url()
    return base[:-4] if base.endswith('/api') else base
