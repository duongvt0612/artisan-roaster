#
# connection.py
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

from PyQt6.QtCore import QSemaphore

from artisanlib import __version__
from typing import Final, Any

import time
import uuid
import datetime
import gzip
import json
import json.decoder
import logging
import dateutil.parser
import requests
import requests.models
import requests.exceptions

from plus import config, account, util

_log: Final[logging.Logger] = logging.getLogger(__name__)


JSON = Any

token_semaphore = QSemaphore(
    1
)  # protects access to the session token which is manipulated only here
refresh_semaphore = QSemaphore(1)

_REFRESH_TOKEN_KEY_PREFIX: Final[str] = 'refresh::'


def _get_refresh_token_key(account: str) -> str:
    return f'{_REFRESH_TOKEN_KEY_PREFIX}{account}'

# request timeout

request_read_timeout_step:Final[int] = 2 # step size to decrease request_read_timeout on success in seconds
request_read_timeout:int = config.read_timeout # dynamic read_timeout, updated on successful communication and timeouts

def getReadTimeout() -> int:
    return request_read_timeout
def updateReadTimeoutOnSuccess() -> None:
    global request_read_timeout # pylint:disable=global-statement
    request_read_timeout = max(config.read_timeout, request_read_timeout - request_read_timeout_step)
def updateReadTimeoutOnTimeout() -> None:
    global request_read_timeout # pylint:disable=global-statement
    request_read_timeout = config.read_timeout_max

#

def getToken() -> str|None:
    try:
        token_semaphore.acquire(1)
        return config.get_token()
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)
        return None
    finally:
        if token_semaphore.available() < 1:
            token_semaphore.release(1)


def getRefreshToken() -> str|None:
    try:
        token_semaphore.acquire(1)
        return config.refresh_token
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)
        return None
    finally:
        if token_semaphore.available() < 1:
            token_semaphore.release(1)


def getNickname() -> str|None:
    try:
        token_semaphore.acquire(1)
        return config.nickname
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)
        return None
    finally:
        if token_semaphore.available() < 1:
            token_semaphore.release(1)


def setToken(token: str, nickname: str|None = None) -> None:
    try:
        token_semaphore.acquire(1)
        config.set_token(token)
        config.nickname = nickname
        aw = config.app_window
        if (aw is not None
            and aw.qmc.operator == ''
            and nickname is not None
            and nickname != ''
        ):  # @UndefinedVariable
            aw.qmc.operator = nickname
    finally:
        if token_semaphore.available() < 1:
            token_semaphore.release(1)


def setRefreshToken(refresh_token: str|None) -> None:
    try:
        token_semaphore.acquire(1)
        config.refresh_token = refresh_token
    finally:
        if token_semaphore.available() < 1:
            token_semaphore.release(1)


def clearCredentials(remove_from_keychain: bool = True) -> None:
    _log.debug('clearCredentials()')
    # remove credentials from keychain
    aw = config.app_window
    try:
        if (
            aw is not None
            and aw.plus_account is not None
            and remove_from_keychain
        ):  # @UndefinedVariable
            try:
                import keyring

                keyring.delete_password(
                    config.app_name, aw.plus_account
                )  # @UndefinedVariable
                keyring.delete_password(
                    config.app_name, _get_refresh_token_key(aw.plus_account)
                )
            except Exception as e:  # pylint: disable=broad-except
                _log.error(e)
    except Exception: # pylint: disable=broad-except
        # config.app_window might be still unbound
        pass
    try:
        token_semaphore.acquire(1)
        config.set_token(None)
        config.refresh_token = None
        if aw is not None:
            aw.plus_account = None
        config.passwd = None
        config.nickname = None
        config.account_nr = None
    finally:
        if token_semaphore.available() < 1:
            token_semaphore.release(1)

def _apply_auth_response(res: JSON, preserve_refresh_token: bool = False) -> bool:
    aw = config.app_window
    if aw is None:
        return False

    payload = res.get('result', res)
    user = payload.get('user', payload.get('data', {}))
    access_token = payload.get('access_token') or user.get('token')
    refresh_token = payload.get('refresh_token')
    if access_token is None:
        return False

    nickname = util.extractInfo(user, 'nickname', None)
    aw.plus_language = util.extractInfo(user, 'language', 'en')
    aw.plus_user_id = util.extractInfo(user, 'user_id', util.extractInfo(user, 'id', None))
    aw.plus_paidUntil = None
    aw.plus_subscription = None
    aw.plus_rlimit = 0
    aw.plus_used = 0

    if 'account' in user:
        res_account = user['account']
        if '_id' in res_account:
            aw.plus_account_id = res_account['_id']
        subscription = util.extractInfo(res_account, 'subscription', '')
        aw.updateSubscriptionSignal.emit(subscription)
        paidUntil = util.extractInfo(res_account, 'paidUntil', '')
        rlimit = -1
        rused = -1
        notifications = 0
        machines = []
        try:
            if 'limit' in user['account']:
                ol = res_account['limit']
                if 'rlimit' in ol:
                    rlimit = ol['rlimit']
                if 'rused' in ol:
                    rused = ol['rused']
        except Exception as e:  # pylint: disable=broad-except
            _log.exception(e)

        if 'notifications' in res:
            notificationDict = res['notifications']
            if notificationDict:
                notifications = util.extractInfo(notificationDict, 'unqualified', 0)
                machines = util.extractInfo(notificationDict, 'machines', [])
            try:
                aw.updateLimitsSignal.emit(rlimit, rused, paidUntil, notifications, machines)
            except Exception as e:  # pylint: disable=broad-except
                _log.exception(e)

        try:
            if paidUntil != '' and (
                dateutil.parser.parse(paidUntil).date()
                - datetime.datetime.now(datetime.UTC).date()
            ).days < (-config.expired_subscription_max_days):
                _log.debug('-> authentication failed due to long expired subscription')
                if 'error' in res:
                    aw.sendmessage(res['error'])
                clearCredentials()
                return False
        except Exception as e:  # pylint: disable=broad-except
            _log.exception(e)

    if 'readonly' in user and isinstance(user['readonly'], bool):
        aw.plus_readonly = user['readonly']
    else:
        aw.plus_readonly = False

    setToken(access_token, nickname)
    if refresh_token is not None:
        setRefreshToken(refresh_token)
    elif not preserve_refresh_token:
        setRefreshToken(None)

    if 'account' in user and '_id' in user['account']:
        account_nr = account.setAccount(user['account']['_id'])
        config.account_nr = account_nr
        _log.debug('-> account: %s', account_nr)
    return True


# returns True on successful authentication
# NOTE: authentify might be called from outside the GUI thread
def authentify() -> bool:
    _log.debug('authentify()')
    try:
        aw = config.app_window
        if (
            aw is not None
            and aw.plus_account is not None
        ):
            if config.passwd is None:
                try:
                    import keyring

                    config.passwd = keyring.get_password(
                        config.app_name, aw.plus_account
                    )
                except Exception as e:  # pylint: disable=broad-except
                    _log.exception(e)
            if config.passwd is None:
                _log.debug('-> password not found')
                clearCredentials()
                return False
            _log.debug('-> authentifying %s', aw.plus_account)
            data = {
                'email': aw.plus_account,
                'username': aw.plus_account,
                'password': config.passwd,
            }
            r = sendData(config.get_auth_url(), data, 'POST', False)
            _log.debug('-> authentifying reply status code: %s', r.status_code)
            if r.status_code != 204 and r.headers['content-type'].strip().startswith('application/json'):
                res = r.json()
                if (
                    ('success' in res and res['success'] and 'result' in res)
                    or 'access_token' in res
                ):
                    return _apply_auth_response(res)
                _log.debug('-> authentication failed')
                if 'error' in res:
                    aw.sendmessage(res['error'])
                clearCredentials()
                return False
            _log.error('204: empty response')
            clearCredentials()
        return False
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as e:
        _log.info(e)
        raise e
    except requests.exceptions.SSLError as e:
        _log.info(e)
        clearCredentials()
        aw = config.app_window
        if aw is not None:
            aw.sendmessage('SSLError')
        raise e
    except requests.exceptions.RequestException as e:
        _log.info(e)
        raise e
    except json.decoder.JSONDecodeError as e:
        if not e.doc:
            raise ValueError('Empty response.') from e
        raise ValueError(f"Decoding error at char {e.pos} (line {e.lineno}, col {e.colno}): '{e.doc}'") from e
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)
        clearCredentials()
        raise e


def refreshSession() -> bool:
    aw = config.app_window
    if aw is None:
        return False
    refresh_token = getRefreshToken()
    if refresh_token is None:
        return False
    try:
        refresh_semaphore.acquire(1)
        current_refresh_token = getRefreshToken()
        if current_refresh_token is None:
            return False
        response = sendData(
            config.get_refresh_url(),
            {'refreshToken': current_refresh_token},
            'POST',
            False,
        )
        if response.status_code == 204:
            clearCredentials(remove_from_keychain=False)
            return False
        if not response.headers['content-type'].strip().startswith('application/json'):
            clearCredentials(remove_from_keychain=False)
            return False
        return _apply_auth_response(response.json(), preserve_refresh_token=True)
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)
        clearCredentials(remove_from_keychain=False)
        return False
    finally:
        if refresh_semaphore.available() < 1:
            refresh_semaphore.release(1)


def restoreSession() -> bool:
    aw = config.app_window
    if aw is None or aw.plus_email is None:
        return False
    try:
        import keyring

        refresh_token = keyring.get_password(
            config.app_name, _get_refresh_token_key(aw.plus_email)
        )
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)
        return False
    if refresh_token is None:
        return False
    aw.plus_account = aw.plus_email
    setRefreshToken(refresh_token)
    return refreshSession()


def persistRefreshToken(account: str, refresh_token: str|None, remember: bool) -> None:
    try:
        import keyring

        key = _get_refresh_token_key(account)
        if remember and refresh_token is not None:
            keyring.set_password(config.app_name, key, refresh_token)
        else:
            keyring.delete_password(config.app_name, key)
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)


def logout() -> bool:
    token = getToken()
    if token is None:
        clearCredentials()
        return True
    try:
        response = requests.post(
            config.get_logout_url(),
            headers=getHeaders(True),
            verify=config.verify_ssl,
            timeout=(config.connect_timeout, getReadTimeout()),
        )
        return response.status_code < 400
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)
        return False
    finally:
        clearCredentials()


def hasRememberedSession(account: str|None) -> bool:
    if account is None:
        return False
    try:
        import keyring

        return keyring.get_password(
            config.app_name, _get_refresh_token_key(account)
        ) is not None
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)
        return False


def clearRememberedSession(account: str|None) -> None:
    if account is None:
        return
    try:
        import keyring

        keyring.delete_password(config.app_name, _get_refresh_token_key(account))
    except Exception as e:  # pylint: disable=broad-except
        _log.exception(e)


def rememberSession(account: str|None, remember: bool) -> None:
    if account is None:
        return
    if remember:
        persistRefreshToken(account, getRefreshToken(), True)
    else:
        clearRememberedSession(account)
        try:
            import keyring
            keyring.delete_password(config.app_name, account)
        except Exception as e:  # pylint: disable=broad-except
            _log.exception(e)


def isRememberedSessionAvailable(account: str|None) -> bool:
    return hasRememberedSession(account)


def ensureAuthenticatedSession(interactive: bool = True) -> bool:
    aw = config.app_window
    if aw is None:
        return False
    if getToken() is not None and config.connected:
        return True
    if restoreSession():
        config.connected = True
        return True
    if not interactive:
        return False
    return authentify()


def isSessionRestorable() -> bool:
    aw = config.app_window
    if aw is None:
        return False
    return hasRememberedSession(aw.plus_email)


def removeRememberedSession(account: str|None) -> None:
    clearRememberedSession(account)


def getHeaders(
    authorized: bool = True, decompress: bool = True) -> dict[str, str]:
    aw = config.app_window
    if aw is not None:
        os, os_version, os_arch = aw.get_os()  # @UndefinedVariable
        headers = {
            'user-agent': f'Artisan/{__version__} ({os}; {os_version}; {os_arch})',
            'Accept-Charset': 'utf-8'
        }
        try:
            locale = aw.locale_str
            if locale != '':
                assert isinstance(locale, str)
                locale = locale.lower().replace('_', '-')
                headers['Accept-Language'] = locale
        except Exception as e:  # pylint: disable=broad-except
            _log.exception(e)
        if authorized:
            token = getToken()
            if token is not None:
                headers['Authorization'] = f'Bearer {token}'
        if decompress:
            headers[
                'Accept-Encoding'
            ] = 'deflate, compress, gzip'  # identity should not be in here!
        return headers
    return {}

def getHeadersAndData(authorized: bool, compress: bool, jsondata: JSON, verb: str) -> tuple[dict[str, str],bytes]:
    headers = getHeaders(authorized, decompress=compress)
    headers['Content-Type'] = 'application/json; charset=utf-8'
    if verb == 'POST':
        headers['Idempotency-Key'] = uuid.uuid4().hex
    if compress and len(jsondata) > config.post_compression_threshold:
        postdata = gzip.compress(jsondata)
        _log.debug('-> compressed size %s', len(postdata))
        headers['Content-Encoding'] = 'gzip'
    else:
        postdata = jsondata
    return headers, postdata


def sendData(
    url: str,
    data: dict[Any, Any],
    verb: str, # POST or PUT
    authorized: bool = True,
    compress: bool = config.compress_posts,
) -> requests.models.Response:
    # don't log POST data as it might contain credentials!
    _log.debug('sendData(%s,_data_,%s,%s)', url, verb, authorized)
    jsondata = json.dumps(data, indent=None, separators=(',', ':'), ensure_ascii=False).encode('utf8')
    _log.debug('-> size %s', len(jsondata))
#    _log.debug("PRINT jsondata: %s",jsondata)
    headers, postdata = getHeadersAndData(authorized, compress, jsondata, verb)

    try:
        if verb == 'POST':
            r = requests.post(
                url,
                headers=headers,
                data=postdata,
                verify=config.verify_ssl,
                timeout=(config.connect_timeout, getReadTimeout()),
            )
        else:
            r = requests.put(
                url,
                headers=headers,
                data=postdata,
                verify=config.verify_ssl,
                timeout=(config.connect_timeout, getReadTimeout()),
            )
        updateReadTimeoutOnSuccess()
        _log.debug('-> status %s, time %s', r.status_code, r.elapsed.total_seconds())
        if authorized and r.status_code == 401:  # authorisation failed
            _log.debug('-> session token outdated (401)')
            if refreshSession():
                time.sleep(0.3) # a little delay not to stress out the server too much
                headers, postdata = getHeadersAndData(
                    authorized, compress, jsondata, verb
                )  # recreate header with new token
                if verb == 'POST':
                    r = requests.post(
                        url,
                        headers=headers,
                        data=postdata,
                        verify=config.verify_ssl,
                        timeout=(config.connect_timeout, getReadTimeout()),
                    )
                else:
                    r = requests.put(
                        url,
                        headers=headers,
                        data=postdata,
                        verify=config.verify_ssl,
                        timeout=(config.connect_timeout, getReadTimeout()),
                    )
                updateReadTimeoutOnSuccess()
                _log.debug('on retry: -> status %s, time %s', r.status_code, r.elapsed.total_seconds())
        return r
    except requests.exceptions.Timeout as e:
        _log.error(e)
        updateReadTimeoutOnTimeout()
        raise e


def getData(url: str, authorized: bool = True, params:dict[str,str]|None = None) -> requests.models.Response|None:
    _log.debug('getData(%s,%s,%s)', url, authorized, params)
    headers = getHeaders(authorized)
    params = params or {}
    #    _log.debug("-> request headers %s",headers)
    try:
        r:requests.models.Response = requests.get(
            url,
            headers=headers,
            verify=config.verify_ssl,
            params=params,
            timeout=(config.connect_timeout, getReadTimeout()),
        )
        updateReadTimeoutOnSuccess()
        _log.debug('-> status %s', r.status_code)
        # _log.debug("-> headers %s",r.headers)
        _log.debug('-> time %s', r.elapsed.total_seconds())
        if authorized and r.status_code == 401:  # authorisation failed
            _log.debug(
                '-> session token outdated (401) - refresh session'
            )
            if refreshSession():
                time.sleep(0.3) # a little delay not to stress out the server too much
                headers = getHeaders(authorized)  # recreate header with new token
                r = requests.get(
                    url,
                    headers=headers,
                    verify=config.verify_ssl,
                    params=params,
                    timeout=(config.connect_timeout, getReadTimeout()),
                )
                updateReadTimeoutOnSuccess()
                _log.debug('-> status %s', r.status_code)
                _log.debug(
                    'on retry: -> time %s', r.elapsed.total_seconds()
                )
        try:
            _log.debug('-> size %s', len(r.content))
    #        _log.debug("-> data %s",r.json())
        except Exception:  # pylint: disable=broad-except
            pass
        return r
    except requests.exceptions.Timeout as e:
        _log.error(e)
        updateReadTimeoutOnTimeout()
        return None
