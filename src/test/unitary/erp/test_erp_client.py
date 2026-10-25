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
        with patch('erp.client._http.post', return_value=_response(200, body)) as post:
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
        with patch('erp.client._http.post', return_value=_response(201, body)):
            assert client.login('admin', 'secret1').access_token == 'a'

    def test_bad_credentials(self) -> None:
        body = _fail('Tên đăng nhập hoặc mật khẩu không đúng')
        with patch('erp.client._http.post', return_value=_response(401, body)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'wrong1')
        assert exc.value.kind == 'invalid_credentials'
        assert exc.value.message == 'Tên đăng nhập hoặc mật khẩu không đúng'

    def test_disabled_account(self) -> None:
        body = _fail('Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên')
        with patch('erp.client._http.post', return_value=_response(401, body)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'disabled'

    def test_validation_error(self) -> None:
        with patch('erp.client._http.post', return_value=_response(400, _fail('username too short'))), pytest.raises(client.ErpAuthError) as exc:
            client.login('ab', 'secret1')
        assert exc.value.kind == 'invalid_credentials'
        assert exc.value.message == 'username too short'

    def test_rate_limited(self) -> None:
        with patch('erp.client._http.post', return_value=_response(429, None, json_error=True)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'rate_limited'

    def test_server_error(self) -> None:
        with patch('erp.client._http.post', return_value=_response(500, None, json_error=True)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'server'

    @pytest.mark.parametrize('exc_type', [requests.exceptions.ConnectionError, requests.exceptions.Timeout])
    def test_network_error(self, exc_type:type[Exception]) -> None:
        with patch('erp.client._http.post', side_effect=exc_type('boom')), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'network'

    def test_non_json_success_is_server_error(self) -> None:
        with patch('erp.client._http.post', return_value=_response(200, None, json_error=True)), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'server'

    def test_success_without_tokens_is_server_error(self) -> None:
        with patch('erp.client._http.post', return_value=_response(200, _ok({'user': USER}))), pytest.raises(client.ErpAuthError) as exc:
            client.login('admin', 'secret1')
        assert exc.value.kind == 'server'

    def test_secrets_are_not_logged(self, caplog:pytest.LogCaptureFixture) -> None:
        body = _ok({'accessToken': 'ACCESS-SECRET', 'refreshToken': 'REFRESH-SECRET', 'user': USER})
        with caplog.at_level('DEBUG'), patch('erp.client._http.post', return_value=_response(200, body)):
            client.login('admin', 'PASSWORD-SECRET')
        text = caplog.text
        assert 'ACCESS-SECRET' not in text
        assert 'REFRESH-SECRET' not in text
        assert 'PASSWORD-SECRET' not in text


class TestRefresh:
    def test_success_returns_the_rotated_refresh_token(self) -> None:
        body = _ok({'accessToken': 'a2', 'refreshToken': 'r2', 'sessionId': 's1', 'user': USER})
        with patch('erp.client._http.post', return_value=_response(200, body)) as post:
            result = client.refresh('r1')
        assert result.access_token == 'a2'
        assert result.refresh_token == 'r2'
        assert result.user == USER
        args, kwargs = post.call_args
        assert args[0] == config.get_refresh_url()
        assert kwargs['json'] == {'refreshToken': 'r1'}

    @pytest.mark.parametrize('data', [
        {'accessToken': 'a2', 'user': USER},                          # the ERP always rotates: no new token is an unusable reply
        {'accessToken': 'a2', 'refreshToken': '', 'user': USER},
        {'refreshToken': 'r2', 'user': USER},
    ])
    def test_reply_without_a_new_refresh_token_is_a_server_error(self, data:dict[str, Any]) -> None:
        with patch('erp.client._http.post', return_value=_response(200, _ok(data))), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('r1')
        assert exc.value.kind == 'server'

    def test_ended_session_carries_its_reason(self) -> None:
        body = {'success': False, 'errors': ['SESSION_REVOKED', 'reuse_detected'], 'errorMessages': ['Phát hiện bất thường'], 'data': None}
        with patch('erp.client._http.post', return_value=_response(401, body)), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('r1')
        assert exc.value.kind == 'invalid_session'
        assert exc.value.reason == 'reuse_detected'

    def test_plain_401_has_no_reason(self) -> None:
        with patch('erp.client._http.post', return_value=_response(401, _fail('hết hạn'))), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('r1')
        assert exc.value.reason is None

    def test_invalid_refresh_token(self) -> None:
        with patch('erp.client._http.post', return_value=_response(401, _fail('Phiên đăng nhập không hợp lệ'))), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('bad')
        assert exc.value.kind == 'invalid_session'

    def test_disabled_account_on_refresh(self) -> None:
        body = _fail('Tài khoản không tồn tại hoặc đã bị khóa')
        with patch('erp.client._http.post', return_value=_response(401, body)), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('r1')
        assert exc.value.kind == 'invalid_session'

    def test_network_error_is_not_an_invalid_session(self) -> None:
        with patch('erp.client._http.post', side_effect=requests.exceptions.ConnectionError('down')), pytest.raises(client.ErpAuthError) as exc:
            client.refresh('r1')
        assert exc.value.kind == 'network'


class TestHeaders:
    """The ERP answers 400 to a login / refresh without X-Client-Type, and shows X-Device-Name in the list of sessions."""

    def test_login_and_refresh_say_who_they_are(self) -> None:
        body = _ok({'accessToken': 'a', 'refreshToken': 'r', 'user': USER})
        with patch('erp.client._http.post', return_value=_response(200, body)) as post, patch('erp.client.device.device_name', return_value='Máy rang 1'):
            client.login('admin', 'secret1')
            client.refresh('r')
        for call in post.call_args_list:
            assert call.kwargs['headers']['X-Client-Type'] == 'artisan'
            assert call.kwargs['headers']['X-Device-Name'] == 'M?y rang 1' # latin-1 safe: what a header can carry

    def test_logout_keeps_the_bearer_header_next_to_them(self) -> None:
        with patch('erp.client._http.post', return_value=_response(200, _ok({}))) as post:
            client.logout('a1', session_id='s1')
        headers = post.call_args.kwargs['headers']
        assert headers['X-Client-Type'] == 'artisan'
        assert headers['Authorization'] == 'Bearer a1'

    def test_authenticated_requests_carry_them_too(self) -> None:
        with patch('erp.client._http.request', return_value=_response(200, {})) as req:
            client.request('GET', 'http://x/api/roasters', 'a1')
        headers = req.call_args.kwargs['headers']
        assert headers['X-Client-Type'] == 'artisan'
        assert headers['Authorization'] == 'Bearer a1'


class TestRevokedReason:
    def test_reads_the_reason(self) -> None:
        assert client.revoked_reason({'errors': ['SESSION_REVOKED', 'kicked_admin']}) == 'kicked_admin'

    def test_reason_may_be_missing(self) -> None:
        assert client.revoked_reason({'errors': ['SESSION_REVOKED']}) == ''

    @pytest.mark.parametrize('body', [None, [], {}, {'errors': []}, {'errors': ['x', 'SESSION_REVOKED']}, {'errors': 'SESSION_REVOKED'}])
    def test_anything_else_is_not_a_revoked_session(self, body:Any) -> None:
        assert client.revoked_reason(body) is None

    def test_known_and_unknown_reasons_have_a_text(self) -> None:
        assert 'quản trị viên' in client.revoked_text('kicked_admin')
        assert client.revoked_text('') == client.revoked_text('something_new')


class TestLogout:
    """Artisan's own logout: gives the roaster of THIS session back and revokes THIS refresh token. The user stays logged in on the web / mobile."""

    def test_sends_bearer_token_and_only_the_lease_session(self) -> None:
        with patch('erp.client._http.post', return_value=_response(200, _ok({'message': 'ok'}))) as post:
            assert client.logout('a1', session_id='s1') is True
        args, kwargs = post.call_args
        assert args[0] == config.get_logout_url()
        assert kwargs['headers']['Authorization'] == 'Bearer a1'
        # a refreshToken in the body is refused by the ERP with 400: it knows the login session from the access token
        assert kwargs['json'] == {'sessionId': 's1'}

    def test_does_not_use_the_logout_that_revokes_every_device(self) -> None:
        with patch('erp.client._http.post', return_value=_response(200, _ok({'message': 'ok'}))) as post:
            client.logout('a1', session_id='s1')
        assert post.call_args[0][0].endswith('/artisan/logout')
        assert not post.call_args[0][0].endswith('/auth/logout')

    def test_is_best_effort(self) -> None:
        with patch('erp.client._http.post', side_effect=requests.exceptions.ConnectionError('down')):
            assert client.logout('a1', session_id='s1') is False
        with patch('erp.client._http.post', return_value=_response(401, _fail('Unauthorized'))):
            assert client.logout('a1', session_id='s1') is False


class TestSharedConnection:
    def test_one_session_serves_every_request(self) -> None:
        # a new TCP/TLS connection per call is what the heartbeat (every 10 s) and the search (every key) must not cost
        assert isinstance(client._http, requests.Session)

    def test_connections_are_kept_alive_and_pooled(self) -> None:
        adapter = client._http.get_adapter('https://erp.test/api')
        assert adapter._pool_maxsize >= 8  # pylint: disable=protected-access
        assert client._http.headers.get('Connection', '').lower() != 'close'
