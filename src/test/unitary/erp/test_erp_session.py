"""Unit tests for erp.session: token state and "remember me" storage in the keyring."""

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from erp import client, config, device, session as session_module
from erp.session import Session

USER = {'id': 'u1', 'username': 'admin', 'fullName': 'Admin', 'roles': []}


class FakeKeyring:
    def __init__(self) -> None:
        self.store:dict[tuple[str, str], str] = {}
        self.broken:bool = False

    def get_password(self, service:str, key:str) -> str|None:
        if self.broken:
            raise RuntimeError('no keyring backend')
        return self.store.get((service, key))

    def set_password(self, service:str, key:str, value:str) -> None:
        if self.broken:
            raise RuntimeError('no keyring backend')
        self.store[(service, key)] = value

    def delete_password(self, service:str, key:str) -> None:
        if self.broken:
            raise RuntimeError('no keyring backend')
        self.store.pop((service, key), None)


@pytest.fixture
def kr() -> Generator[FakeKeyring, None, None]:
    fake = FakeKeyring()
    with patch.object(session_module, '_keyring', return_value=fake):
        yield fake


def _result(access:str = 'a1', refresh:str = 'r1') -> client.AuthResult:
    return client.AuthResult(access, refresh, USER)


def _stored(kr:FakeKeyring) -> dict[str, str]:
    return {key: value for (service, key), value in kr.store.items() if service == config.keyring_service()}


class TestLogin:
    def test_login_sets_state(self, kr:FakeKeyring) -> None:
        s = Session()
        with patch('erp.session.client.login', return_value=_result()) as login:
            user = s.login('admin', 'secret1', remember=False)
        login.assert_called_once_with('admin', 'secret1')
        assert user == USER
        assert s.is_authenticated
        assert s.access_token == 'a1'
        assert s.user == USER
        assert _stored(kr) == {}

    def test_login_with_remember_stores_refresh_token_and_username(self, kr:FakeKeyring) -> None:
        s = Session()
        with patch('erp.session.client.login', return_value=_result()):
            s.login('admin', 'secret1', remember=True)
        assert _stored(kr) == {'refresh_token': 'r1', 'username': 'admin'}

    def test_login_without_remember_removes_previous_stored_token(self, kr:FakeKeyring) -> None:
        kr.store[(config.keyring_service(), 'refresh_token')] = 'old'
        kr.store[(config.keyring_service(), 'username')] = 'old_user'
        s = Session()
        with patch('erp.session.client.login', return_value=_result()):
            s.login('admin', 'secret1', remember=False)
        assert _stored(kr) == {}

    def test_failed_login_leaves_state_unauthenticated(self, kr:FakeKeyring) -> None:
        s = Session()
        with patch('erp.session.client.login', side_effect=client.ErpAuthError('invalid_credentials', 'bad')), pytest.raises(client.ErpAuthError):
            s.login('admin', 'wrong1', remember=True)
        assert not s.is_authenticated
        assert s.access_token is None
        assert _stored(kr) == {}

    def test_broken_keyring_does_not_break_login(self, kr:FakeKeyring) -> None:
        kr.broken = True
        s = Session()
        with patch('erp.session.client.login', return_value=_result()):
            s.login('admin', 'secret1', remember=True)
        assert s.is_authenticated


class TestRestore:
    def test_nothing_stored(self, kr:FakeKeyring) -> None: # noqa: ARG002
        s = Session()
        with patch('erp.session.client.refresh') as refresh:
            assert s.restore() is False
        refresh.assert_not_called()
        assert not s.is_authenticated

    def test_success(self, kr:FakeKeyring) -> None:
        kr.store[(config.keyring_service(), 'refresh_token')] = 'r1'
        s = Session()
        with patch('erp.session.client.refresh', return_value=_result('a2', 'r1')) as refresh:
            assert s.restore() is True
        refresh.assert_called_once_with('r1')
        assert s.is_authenticated
        assert s.access_token == 'a2'

    def test_rotated_refresh_token_is_stored(self, kr:FakeKeyring) -> None:
        kr.store[(config.keyring_service(), 'refresh_token')] = 'r1'
        s = Session()
        with patch('erp.session.client.refresh', return_value=_result('a2', 'r2')):
            s.restore()
        assert _stored(kr)['refresh_token'] == 'r2'

    def test_invalid_session_clears_stored_token_but_keeps_username(self, kr:FakeKeyring) -> None:
        kr.store[(config.keyring_service(), 'refresh_token')] = 'r1'
        kr.store[(config.keyring_service(), 'username')] = 'admin'
        s = Session()
        with patch('erp.session.client.refresh', side_effect=client.ErpAuthError('invalid_session', 'expired')):
            assert s.restore() is False
        assert not s.is_authenticated
        assert _stored(kr) == {'username': 'admin'}

    @pytest.mark.parametrize('kind', ['network', 'server', 'rate_limited'])
    def test_transient_errors_propagate_and_keep_the_stored_token(self, kr:FakeKeyring, kind:Any) -> None:
        kr.store[(config.keyring_service(), 'refresh_token')] = 'r1'
        s = Session()
        with patch('erp.session.client.refresh', side_effect=client.ErpAuthError(kind, 'down')), pytest.raises(client.ErpAuthError) as exc:
            s.restore()
        assert exc.value.kind == kind
        assert _stored(kr) == {'refresh_token': 'r1'}
        assert not s.is_authenticated

    def test_broken_keyring_means_nothing_to_restore(self, kr:FakeKeyring) -> None:
        kr.broken = True
        assert Session().restore() is False


class TestLogoutAndClear:
    def test_logout_calls_erp_and_clears_everything(self, kr:FakeKeyring) -> None:
        s = Session()
        with patch('erp.session.client.login', return_value=_result()):
            s.login('admin', 'secret1', remember=True)
        with patch('erp.session.client.logout', return_value=True) as logout:
            s.logout()
        # the ERP is told WHICH session / refresh token ends: nothing else of the user is logged out
        logout.assert_called_once_with('a1', session_id=device.session_id(), refresh_token='r1')
        assert not s.is_authenticated
        assert s.user is None
        assert _stored(kr) == {}

    def test_logout_without_session_does_not_call_erp(self, kr:FakeKeyring) -> None: # noqa: ARG002
        s = Session()
        with patch('erp.session.client.logout') as logout:
            s.logout()
        logout.assert_not_called()

    def test_clear_keeps_the_username_for_prefill(self, kr:FakeKeyring) -> None:
        kr.store[(config.keyring_service(), 'refresh_token')] = 'r1'
        kr.store[(config.keyring_service(), 'username')] = 'admin'
        s = Session()
        s.clear(keep_username=True)
        assert _stored(kr) == {'username': 'admin'}

    def test_stored_username(self, kr:FakeKeyring) -> None:
        assert Session().stored_username() is None
        kr.store[(config.keyring_service(), 'username')] = 'admin'
        assert Session().stored_username() == 'admin'


class TestEnvironmentIsolation:
    """Two builds (production, staging) on one PC share the OS keychain: neither may see or overwrite the other's login."""

    def test_a_login_of_one_environment_is_invisible_to_the_other(self, monkeypatch:pytest.MonkeyPatch, kr:FakeKeyring) -> None:
        monkeypatch.setenv(config.ENV_ENVIRONMENT, 'staging')
        config.reset_cache()
        staging = Session()
        with patch('erp.session.client.login', return_value=client.AuthResult('a1', 'r-staging', USER)):
            staging.login('admin', 'secret1', remember=True)
        assert {(svc, key) for (svc, key) in kr.store} == {('lvg-erp:staging', 'refresh_token'), ('lvg-erp:staging', 'username')}

        monkeypatch.setenv(config.ENV_ENVIRONMENT, 'production')
        config.reset_cache()
        production = Session()
        assert production.stored_username() is None
        with patch('erp.session.client.refresh') as refresh:
            assert production.restore() is False   # nothing stored for production: no refresh is even attempted
        refresh.assert_not_called()

    def test_logging_out_of_one_environment_keeps_the_other(self, monkeypatch:pytest.MonkeyPatch, kr:FakeKeyring) -> None:
        kr.store[('lvg-erp:production', 'refresh_token')] = 'r-prod'
        monkeypatch.setenv(config.ENV_ENVIRONMENT, 'staging')
        config.reset_cache()
        kr.store[('lvg-erp:staging', 'refresh_token')] = 'r-stg'
        Session().clear()
        assert ('lvg-erp:staging', 'refresh_token') not in kr.store
        assert kr.store[('lvg-erp:production', 'refresh_token')] == 'r-prod'
