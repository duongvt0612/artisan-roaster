"""Unit tests for erp.session.Session.authed_request: Bearer requests that refresh the 15 minute access token on 401."""

import threading
from collections.abc import Generator
from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

from erp import client, session as session_module
from erp.session import Session

USER = {'id': 'u1', 'username': 'admin', 'fullName': 'Admin', 'roles': []}


class FakeKeyring:
    def __init__(self) -> None:
        self.store:dict[tuple[str, str], str] = {}

    def get_password(self, service:str, key:str) -> str|None:
        return self.store.get((service, key))

    def set_password(self, service:str, key:str, value:str) -> None:
        self.store[(service, key)] = value

    def delete_password(self, service:str, key:str) -> None:
        self.store.pop((service, key), None)


@pytest.fixture(autouse=True)
def kr() -> Generator[FakeKeyring, None, None]:
    fake = FakeKeyring()
    with patch.object(session_module, '_keyring', return_value=fake):
        yield fake


def _response(status:int, body:Any = None) -> Mock:
    r = Mock(spec=requests.Response)
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


def _logged_in(remember:bool = False) -> Session:
    s = Session()
    with patch('erp.session.client.login', return_value=client.AuthResult('a1', 'r1', USER)):
        s.login('admin', 'secret1', remember=remember)
    return s


def test_sends_the_bearer_token_and_returns_the_response() -> None:
    s = _logged_in()
    with patch('erp.client.requests.request', return_value=_response(200, {'ok': True})) as req:
        r = s.authed_request('GET', 'http://erp/api/roasters', params={'sessionId': 's1'})
    assert r.status_code == 200
    args, kwargs = req.call_args
    assert args == ('GET', 'http://erp/api/roasters')
    assert kwargs['headers']['Authorization'] == 'Bearer a1'
    assert kwargs['params'] == {'sessionId': 's1'}
    assert kwargs['timeout'] is not None


def test_refreshes_once_on_401_and_retries_with_the_new_token() -> None:
    s = _logged_in(remember=False)  # NOT remembered: the refresh token must still be usable from memory
    replies = [_response(401), _response(200, {'ok': True})]
    with patch('erp.client.requests.request', side_effect=replies) as req, \
         patch('erp.session.client.refresh', return_value=client.AuthResult('a2', 'r1', USER)) as refresh:
        r = s.authed_request('POST', 'http://erp/api/x', json={'a': 1})
    assert r.status_code == 200
    refresh.assert_called_once_with('r1')
    assert s.access_token == 'a2'
    first, second = req.call_args_list
    assert first.kwargs['headers']['Authorization'] == 'Bearer a1'
    assert second.kwargs['headers']['Authorization'] == 'Bearer a2'
    assert second.kwargs['json'] == {'a': 1}


def test_a_second_401_after_refresh_is_returned_not_looped() -> None:
    s = _logged_in()
    with patch('erp.client.requests.request', side_effect=[_response(401), _response(401)]) as req, \
         patch('erp.session.client.refresh', return_value=client.AuthResult('a2', 'r1', USER)):
        r = s.authed_request('GET', 'http://erp/api/x')
    assert r.status_code == 401
    assert req.call_count == 2


def test_dead_refresh_token_logs_out_and_raises() -> None:
    s = _logged_in(remember=True)
    with patch('erp.client.requests.request', return_value=_response(401)), \
         patch('erp.session.client.refresh', side_effect=client.ErpAuthError('invalid_session', 'expired')), \
         pytest.raises(client.ErpAuthError) as exc:
        s.authed_request('GET', 'http://erp/api/x')
    assert exc.value.kind == 'invalid_session'
    assert not s.is_authenticated


def test_refresh_hitting_the_network_error_keeps_the_session() -> None:
    s = _logged_in()
    with patch('erp.client.requests.request', return_value=_response(401)), \
         patch('erp.session.client.refresh', side_effect=client.ErpAuthError('network', 'down')), \
         pytest.raises(client.ErpAuthError) as exc:
        s.authed_request('GET', 'http://erp/api/x')
    assert exc.value.kind == 'network'
    assert s.is_authenticated  # a network blip must not log the user out


@pytest.mark.parametrize('exc_type', [requests.exceptions.ConnectionError, requests.exceptions.Timeout])
def test_network_errors_are_reported_as_such(exc_type:type[Exception]) -> None:
    s = _logged_in()
    with patch('erp.client.requests.request', side_effect=exc_type('boom')), \
         pytest.raises(client.ErpAuthError) as exc:
        s.authed_request('GET', 'http://erp/api/x')
    assert exc.value.kind == 'network'


def test_not_logged_in_raises_without_touching_the_network() -> None:
    with patch('erp.client.requests.request') as req, \
         pytest.raises(client.ErpAuthError) as exc:
        Session().authed_request('GET', 'http://erp/api/x')
    assert exc.value.kind == 'invalid_session'
    req.assert_not_called()


def test_concurrent_401s_refresh_only_once() -> None:
    """The heartbeat thread and the GUI thread can hit the expired token together; the second one must reuse the first refresh."""
    s = _logged_in()
    gate = threading.Barrier(2)
    refreshed = threading.Event()

    def fake_request(_method:str, _url:str, **kwargs:Any) -> Mock:
        token = kwargs['headers']['Authorization']
        if token == 'Bearer a1':
            try:
                gate.wait(timeout=2)  # both threads got their 401 with the stale token
            except threading.BrokenBarrierError:
                pass
            return _response(401)
        return _response(200)

    def slow_refresh(_token:str) -> client.AuthResult:
        refreshed.wait(0.05)
        return client.AuthResult('a2', 'r1', USER)

    results:list[int] = []
    with patch('erp.client.requests.request', side_effect=fake_request), \
         patch('erp.session.client.refresh', side_effect=slow_refresh) as refresh:
        threads = [threading.Thread(target=lambda: results.append(s.authed_request('GET', 'http://erp/api/x').status_code)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
    assert sorted(results) == [200, 200]
    assert refresh.call_count == 1


def _jwt(exp:float) -> str:
    import base64
    import json

    def part(obj:dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip('=')

    return f"{part({'alg': 'HS256'})}.{part({'sub': 'u1', 'exp': exp})}.sig"


class TestFreshAccessToken:
    def test_returns_the_current_token_while_it_has_time_left(self) -> None:
        import time
        s = Session()
        s.access_token = _jwt(time.time() + 600)
        s.refresh_token = 'r1'
        with patch('erp.session.client.refresh') as refresh:
            assert s.fresh_access_token() == s.access_token
        refresh.assert_not_called()

    def test_renews_a_token_that_expires_within_a_minute(self) -> None:
        import time
        s = Session()
        s.access_token = _jwt(time.time() + 20)
        s.refresh_token = 'r1'
        new = _jwt(time.time() + 900)
        with patch('erp.session.client.refresh', return_value=client.AuthResult(new, 'r1', USER)):
            assert s.fresh_access_token() == new

    def test_renews_an_already_expired_token(self) -> None:
        import time
        s = Session()
        s.access_token = _jwt(time.time() - 5)
        s.refresh_token = 'r1'
        new = _jwt(time.time() + 900)
        with patch('erp.session.client.refresh', return_value=client.AuthResult(new, 'r1', USER)):
            assert s.fresh_access_token() == new

    def test_an_unreadable_token_is_treated_as_expired(self) -> None:
        s = Session()
        s.access_token = 'not-a-jwt'
        s.refresh_token = 'r1'
        with patch('erp.session.client.refresh', return_value=client.AuthResult('a2', 'r1', USER)):
            assert s.fresh_access_token() == 'a2'

    def test_none_when_not_logged_in_or_the_session_is_dead(self) -> None:
        assert Session().fresh_access_token() is None
        import time
        s = Session()
        s.access_token = _jwt(time.time() - 5)
        s.refresh_token = 'r1'
        with patch('erp.session.client.refresh', side_effect=client.ErpAuthError('invalid_session', 'expired')):
            assert s.fresh_access_token() is None

    def test_a_network_blip_still_returns_the_old_token(self) -> None:
        import time
        s = Session()
        old = _jwt(time.time() - 5)
        s.access_token = old
        s.refresh_token = 'r1'
        with patch('erp.session.client.refresh', side_effect=client.ErpAuthError('network', 'down')):
            assert s.fresh_access_token() == old  # let the caller try; the server will refuse it if it is really expired


class TestMultipart:
    """Uploads go through the same renew-on-401 path; the file parts are bytes so they can be sent again."""

    def test_data_and_files_reach_the_http_call_and_are_resent_after_a_renewal(self) -> None:
        s = _logged_in()
        parts = {'file': ('a.pdf', b'%PDF-', 'application/pdf')}
        with patch('erp.session.client.refresh', return_value=client.AuthResult('a2', 'r1', USER)), \
             patch('erp.client.requests.request', side_effect=[_response(401), _response(200, {'ok': True})]) as req:
            r = s.authed_request('POST', 'http://erp/api/artisan/rounds/r1/files', data={'kind': 'pdf'}, files=parts)
        assert r.status_code == 200
        assert req.call_count == 2
        for call, token in zip(req.call_args_list, ('a1', 'a2'), strict=True):
            assert call.kwargs['headers']['Authorization'] == f'Bearer {token}'
            assert call.kwargs['data'] == {'kind': 'pdf'} and call.kwargs['files'] == parts
            assert call.kwargs['timeout'] == client.config.upload_timeout   # a file takes longer than a JSON call

    def test_plain_requests_keep_their_shape_and_timeout(self) -> None:
        s = _logged_in()
        with patch('erp.client.requests.request', return_value=_response(200)) as req:
            s.authed_request('GET', 'http://erp/api/roasters', params={'a': 1})
        kwargs = req.call_args.kwargs
        assert kwargs['files'] is None and kwargs['data'] is None and kwargs['timeout'] == client.config.timeout
