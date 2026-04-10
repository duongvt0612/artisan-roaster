#!/usr/bin/python
#
# config.py
#
# Copyright (c) 2023, Paul Holleis, Marko Luther
# All rights reserved.
#
#
# ABOUT
# This module connects to the artisan.plus inventory management service

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
from typing import Final, TYPE_CHECKING

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # pylint: disable=unused-import

# Constants
app_name: Final[str] = 'artisan.plus'
profile_ext: Final[str] = 'alog'
uuid_tag: Final[str] = 'roastUUID' # as used in .alog profiles, send as 'roast_id' as part of the sync record to the server
schedule_uuid_tag: Final[str] = 'scheduleID' # send as 's_item_id' as part of the sync record to the server
schedule_date_tag: Final[str] = 'scheduleDate' # send as 's_item_date' as part of the sync record to the server

# Service URLs


def _normalized_base_url(value: str) -> str:
    return value.rstrip('/')


def _env_base_url(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return _normalized_base_url(value)


def get_api_base_url() -> str:
    return _env_base_url('ARTISAN_PLUS_API_BASE_URL', 'https://artisan.plus/api/v1')



def get_web_base_url() -> str:
    return _env_base_url('ARTISAN_PLUS_WEB_BASE_URL', 'https://artisan.plus')



def get_register_url() -> str:
    return get_web_base_url() + '/register'



def get_reset_passwd_url() -> str:
    return get_web_base_url() + '/resetPassword'



def get_user_guide_url() -> str:
    return get_web_base_url() + '/user-guide'



def get_auth_url() -> str:
    return get_api_base_url() + '/accounts/users/authenticate'



def get_refresh_url() -> str:
    return get_api_base_url() + '/auth/refresh'



def get_logout_url() -> str:
    return get_api_base_url() + '/auth/logout'



def get_stock_url() -> str:
    return get_api_base_url() + '/acoffees'



def get_roast_url() -> str:
    return get_api_base_url() + '/aroast'



def get_lock_schedule_url() -> str:
    return get_api_base_url() + '/aschedule/lock'



def get_notifications_url() -> str:
    return get_api_base_url() + '/notifications'


api_base_url: str = get_api_base_url()
web_base_url: str = get_web_base_url()

shop_base_url: Final[str] = 'https://buy.artisan.plus/'

register_url: str = get_register_url()
reset_passwd_url: str = get_reset_passwd_url()
user_guide_url: str = get_user_guide_url()
auth_url: str = get_auth_url()
refresh_url: str = get_refresh_url()
logout_url: str = get_logout_url()
stock_url: str = get_stock_url()
roast_url: str = get_roast_url()
lock_schedule_url: str = get_lock_schedule_url()
notifications_url: str = get_notifications_url()

# documented local runtime override values:
# ARTISAN_PLUS_API_BASE_URL=http://localhost:10301/api
# ARTISAN_PLUS_WEB_BASE_URL=http://localhost:10300

# Connection configurations

#verify_ssl: Final[bool] = False
verify_ssl: Final[bool] = True
connect_timeout: Final[int] = 6  # in seconds
read_timeout: Final[int] = 12  # in seconds
read_timeout_max: Final[int] = 30  # in seconds
min_passwd_len: Final[int] = 4
min_login_len: Final[int] = 6
compress_posts: Final[bool] = True
# post_compression_threshold holds the number in bytes before compression
# kicks in
# (data smaller than this are always send uncompressed via POST)
post_compression_threshold: Final[int] = 500

# Authentication configuration

# do not authentify successfully after max_days after the subscription expired
expired_subscription_max_days: Final[int] = 90

# Cache and queue parameters

# Note: stock_cache_expiration should be larger than schedule_cache_expiration
stock_cache_expiration: Final[int] = 35   # expiration period in seconds for full stock updates (expensive)
schedule_cache_expiration: Final[int] = 5 # expiration period in seconds for full stock updates only in case the schedule on the server has changed

queue_start_delay: Final[int] = 5  # startup time of queue in seconds
# delay between tasks in seconds (cycling interval of the queue)
queue_task_delay: Final[float] = 2.0
queue_retries: Final[int] = 2  # number of retries (should be >=0)
queue_retry_delay: Final[int] = 30  # time between retries in seconds
queue_discard_after: Final[int] = 3*24*60*60 # period in seconds after 'modified_at'..
# .. until a queued item is removed from the queue; if queue_discard_after is 0 items are never discarded
# queque_put_timeout indicates the number of seconds to wait on putting
# a new item into the queue (unused for now)
queue_put_timeout: Final[float] = 0.5


# AppData

# the stock cache reflects the current coffee stock of the account and
# gets automatically synced with the cloud
stock_cache: Final[str] = 'cache'

# the completed roasts cache reflects the last roasted scheduled items
completed_roasts_cache: Final[str] = 'completed'

# the prepared items cache reflects the prepared scheduled items
prepared_items_cache: Final[str] = 'prepared'

# the hidden items cache reflects the hidden scheduled items
hidden_items_cache: Final[str] = 'hidden'

# the uuid register that associates UUIDs with local filepaths where to
# locate the corresponding Artisan profiles
uuid_cache: Final[str] = 'uuids'

# the account register that associates account ids with a local running
# account number
# Note: the account_cache file is shared between the main Artisan and the
# ArtisanViewer app, protected by a filelock
account_cache: Final[str] = 'account'

# the account nr locally associated to the current account, or None
account_nr: int|None = None

# the sync register that associates UUIDs with last known modification dates
# modified_at for profiles uploaded/synced automatically
# Note: the sync_cache file is shared between the main Artisan and the
# ArtisanViewer app, protected by a filelock
sync_cache: Final[str] = 'sync'

# the outbox queues the outgoing PUSH/PUT data requests
# Note: the outbox_cache file is shared between the main Artisan and the
# ArtisanViewer app, NOT protected by an extra filelock
outbox_cache: Final[str] = 'outbox'


# Runtime variables

app_window: 'ApplicationWindow|None' = None  # handle to the main Artisan application window
#   if set, app_window.plus_login holds the current login account if any and
#   app_window.updatePlusIcon() is a function that updates the toolbar
#   plus service connection indicator icon
connected: bool = False  # connection status
passwd: str|None = None
# the current access token
access_token: str|None = None
# refresh token used to renew the access token
refresh_token: str|None = None
# login nickname assigned on login with session token
nickname: str|None = None


def get_token() -> str|None:
    return access_token


def set_token(value: str|None) -> None:
    global access_token
    access_token = value



def get_refresh_token() -> str|None:
    return refresh_token



def set_refresh_token(value: str|None) -> None:
    global refresh_token
    refresh_token = value
