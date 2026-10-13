"""Unit tests for erp.client: login, refresh and logout against the LVG ERP API.

All HTTP traffic is mocked, nothing here talks to a real server.
"""

from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

from erp import client, config


def _response(status:int, body:Any = None, json_error:bool = False) -> Mock:
    r = Mock()
    r.status_code = status
    if json_error:
        r.json.side_effect = ValueError('not json')
    else:
        r.json.return_value = body
    return r


def _ok(data:dict[str, Any]) -> dict[str, Any]:
    return {'success': True, 'errors': [], 'errorMessages': [], 'data': data}


def _fail(*messages:str) -> dict[str, Any]:
    return {'success': False, 'errors': ['x'], 'errorMessages': list(messages), 'data': None}


USER = {'id': 'u1', 'username': 'admin', 'fullName': 'Admin', 'roles': []}


class TestConfig:
    def test_env_override_strips_trailing_slash(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv('ERP_API_BASE_URL', 'http://localhost:10301/api/')
        assert config.get_api_base_url() == 'http://localhost:10301/api'
        assert config.get_login_url() == 'http://localhost:10301/api/auth/login'
        assert config.get_refresh_url() == 'http://localhost:10301/api/auth/refresh'
        assert config.get_logout_url() == 'http://localhost:10301/api/artisan/logout'


class TestLogin:
    def test_success_returns_tokens_and_user(self) -> None:
        body = _ok({'accessToken': 'a', 'refreshToken': 'r', 'user': USER})
        with patch('erp.client.requests.post', return_value=_response(200, body)) as post:
            result = client.login('admin', 'secret1')
        assert result.access_token == 'a'
        assert result.refresh_token == 'r'
        assert result.user == USER
        args, kwargs = post.call_args
        assert args[0] == config.get_login_url()
        assert kwargs['json'] == {'username': 'admin', 'password': 'secret1'}
        assert kwargs['timeout'] == config.timeout

    def test_201_is_also_success(self) -> None:
        body = _ok({'accessToken': 'a', 'refreshToken': 'r', 'user': USER})
        with patch('erp.client.requests.post', return_value=_response(201, body)):
            assert client.login('admin', 'secret1').access_token == 'a'

    def test_bad_credentials(self) -> None:
        body = _fail('Tên đăng nhập hoặc mật khẩu không đúng')
        with patch('erp.client.requests.post', return_value=_response(401, body)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'wrong1')
        assert exc.value.kind == 'invalid_credentials'
        assert exc.value.message == 'Tên đăng nhập hoặc mật khẩu không đúng'

    def test_disabled_account(self) -> None:
        body = _fail('Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên')
        with patch('erp.client.requests.post', return_value=_response(401, body)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'disabled'

    def test_validation_error(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(400, _fail('username too short'))), pytest.raises(client.ErpAuthError) as exc:
            client.login('ab', 'secret1')
        assert exc.value.kind == 'invalid_credentials'
        assert exc.value.message == 'username too short'

    def test_rate_limited(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(429, None, json_error=True)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'rate_limited'

    def test_server_error(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(500, None, json_error=True)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'server'

    @pytest.mark.parametrize('exc_type', [requests.exceptions.ConnectionError, requests.exceptions.Timeout])
    def test_network_error(self, exc_type:type[Exception]) -> None:
        with patch('erp.client.requests.post', side_effect=exc_type('boom')), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'network'

    def test_non_json_success_is_server_error(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(200, None, json_error=True)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'server'

    def test_success_without_tokens_is_server_error(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(200, _ok({'user': USER}))), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'server'

    def test_secrets_are_not_logged(self, caplog:pytest.LogCaptureFixture) -> None:
        body = _ok({'accessToken': 'ACCESS-SECRET', 'refreshToken': 'REFRESH-SECRET', 'user': USER})
        with caplog.at_level('DEBUG'), patch('erp.client.requests.post', return_value=_response(200, body)):
            client.login('admin', 'PASSWORD-SECRET')
        text = caplog.text
        assert 'ACCESS-SECRET' not in text
        assert 'REFRESH-SECRET' not in text
        assert 'PASSWORD-SECRET' not in text


class TestRefresh:
    def test_success_keeps_the_given_refresh_token(self) -> None:
        # the ERP refresh endpoint returns only a new access token (no rotation)
        body = _ok({'accessToken': 'a2', 'user': USER})
        with patch('erp.client.requests.post', return_value=_response(200, body)) as post:
            result = client.refresh('r1')
        assert result.access_token == 'a2'
        assert result.refresh_token == 'r1'
        assert result.user == USER
        args, kwargs = post.call_args
        assert args[0] == config.get_refresh_url()
        assert kwargs['json'] == {'refreshToken': 'r1'}

    def test_rotated_refresh_token_is_used_if_server_sends_one(self) -> None:
        body = _ok({'accessToken': 'a2', 'refreshToken': 'r2', 'user': USER})
        with patch('erp.client.requests.post', return_value=_response(200, body)):
            assert client.refresh('r1').refresh_token == 'r2'

    def test_invalid_refresh_token(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(401, _fail('Phiên đăng nhập không hợp lệ'))), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('bad')
        assert exc.value.kind == 'invalid_session'

    def test_disabled_account_on_refresh(self) -> None:
        body = _fail('Tài khoản không tồn tại hoặc đã bị khóa')
        with patch('erp.client.requests.post', return_value=_response(401, body)), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('r1')
        assert exc.value.kind == 'invalid_session'

    def test_network_error_is_not_an_invalid_session(self) -> None:
        with patch('erp.client.requests.post', side_effect=requests.exceptions.ConnectionError('down')), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('r1')
        assert exc.value.kind == 'network'


class TestLogout:
    """Artisan's own logout: gives the roaster of THIS session back and revokes THIS refresh token. The user stays logged in on the web / mobile."""

    def test_sends_bearer_token_session_and_refresh_token(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(200, _ok({'message': 'ok'}))) as post:
            assert client.logout('a1', session_id='s1', refresh_token='r1') is True
        args, kwargs = post.call_args
        assert args[0] == config.get_logout_url()
        assert kwargs['headers']['Authorization'] == 'Bearer a1'
        assert kwargs['json'] == {'sessionId': 's1', 'refreshToken': 'r1'}

    def test_does_not_use_the_logout_that_revokes_every_device(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(200, _ok({'message': 'ok'}))) as post:
            client.logout('a1', session_id='s1', refresh_token='r1')
        assert post.call_args[0][0].endswith('/artisan/logout')
        assert not post.call_args[0][0].endswith('/auth/logout')

    def test_refresh_token_is_optional(self) -> None:
        with patch('erp.client.requests.post', return_value=_response(200, _ok({'message': 'ok'}))) as post:
            assert client.logout('a1', session_id='s1', refresh_token=None) is True
        assert post.call_args[1]['json'] == {'sessionId': 's1'}

    def test_is_best_effort(self) -> None:
        with patch('erp.client.requests.post', side_effect=requests.exceptions.ConnectionError('down')):
            assert client.logout('a1', session_id='s1', refresh_token='r1') is False
        with patch('erp.client.requests.post', return_value=_response(401, _fail('Unauthorized'))):
            assert client.logout('a1', session_id='s1', refresh_token='r1') is False
