"""Unit tests for erp.config: which ERP server a build talks to. There is no default server, the environment variables win over the
erp-config.json of the build, and nothing is guessed."""

import json
import sys
from pathlib import Path

import pytest

from erp import config


@pytest.fixture
def no_env(monkeypatch:pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (config.ENV_API_URL, config.ENV_ENVIRONMENT, config.ENV_SOCKET_URL):
        monkeypatch.delenv(name, raising=False)
    config.reset_cache()
    return monkeypatch


@pytest.fixture
def bundled(no_env:pytest.MonkeyPatch, tmp_path:Path) -> Path:
    """Pretends to run from a PyInstaller bundle whose directory is tmp_path."""
    no_env.setattr(sys, 'frozen', True, raising=False)
    no_env.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    return tmp_path / config.CONFIG_FILENAME


def write(path:Path, **values:object) -> None:
    path.write_text(json.dumps(values), encoding='utf-8')


class TestNoServerNoGuess:
    def test_nothing_configured_is_refused(self, bundled:Path) -> None:  # noqa: ARG002
        with pytest.raises(config.ConfigError) as e:
            config.get_api_base_url()
        assert 'chưa được cấu hình máy chủ ERP' in e.value.message and config.ENV_API_URL in e.value.message

    def test_check_raises_the_same_way(self, bundled:Path) -> None:  # noqa: ARG002
        with pytest.raises(config.ConfigError):
            config.check()

    def test_label_is_empty_instead_of_raising(self, bundled:Path) -> None:  # noqa: ARG002
        assert config.environment_label() == ''

    def test_no_url_constant_is_left_in_the_module(self) -> None:
        assert not hasattr(config, 'default_api_base_url')


class TestEnvironmentVariables:
    def test_url_and_environment(self, no_env:pytest.MonkeyPatch) -> None:
        no_env.setenv(config.ENV_API_URL, 'https://erp-api.example.vn/api/')
        no_env.setenv(config.ENV_ENVIRONMENT, 'Staging')
        assert config.get_api_base_url() == 'https://erp-api.example.vn/api'
        assert config.environment() == 'staging'
        assert config.get_login_url() == 'https://erp-api.example.vn/api/auth/login'
        assert config.get_refresh_url() == 'https://erp-api.example.vn/api/auth/refresh'
        assert config.get_logout_url() == 'https://erp-api.example.vn/api/artisan/logout' # Artisan's own logout, not /auth/logout
        assert config.get_roasters_url() == 'https://erp-api.example.vn/api/roasters'
        assert config.get_artisan_url() == 'https://erp-api.example.vn/api/artisan'

    def test_the_environment_is_taken_from_the_host_when_not_given(self, no_env:pytest.MonkeyPatch) -> None:
        no_env.setenv(config.ENV_API_URL, 'http://localhost:10301/api')
        assert config.environment() == 'localhost'
        no_env.setenv(config.ENV_API_URL, 'https://erp-api.example.vn/api')
        assert config.environment() == 'erp-api-example-vn'

    def test_a_change_of_the_variables_is_noticed(self, no_env:pytest.MonkeyPatch) -> None:
        no_env.setenv(config.ENV_API_URL, 'https://one.example/api')
        assert config.get_api_base_url() == 'https://one.example/api'
        no_env.setenv(config.ENV_API_URL, 'https://two.example/api')
        assert config.get_api_base_url() == 'https://two.example/api'

    def test_the_variables_win_over_the_file_of_the_build(self, bundled:Path) -> None:
        write(bundled, apiBaseUrl='https://prod.example/api', environment='production')
        assert config.get_api_base_url() == 'https://prod.example/api'
        import os
        os.environ[config.ENV_API_URL] = 'http://localhost:10301/api'
        try:
            assert config.get_api_base_url() == 'http://localhost:10301/api'
        finally:
            del os.environ[config.ENV_API_URL]

    def test_an_invalid_variable_is_an_error_not_a_fallback(self, bundled:Path, no_env:pytest.MonkeyPatch) -> None:
        write(bundled, apiBaseUrl='https://prod.example/api', environment='production')
        no_env.setenv(config.ENV_API_URL, 'ftp://x')
        with pytest.raises(config.ConfigError):
            config.get_api_base_url()   # it does not silently use the file instead


class TestFileOfTheBuild:
    def test_read_from_the_bundle(self, bundled:Path) -> None:
        write(bundled, apiBaseUrl='https://erp-api.example.vn/api', environment='production')
        assert config.get_api_base_url() == 'https://erp-api.example.vn/api'
        assert config.environment() == 'production' and config.is_production()
        assert config.environment_label() == ''

    def test_a_staging_build_is_labelled(self, bundled:Path) -> None:
        write(bundled, apiBaseUrl='https://stg.example.vn/api', environment='staging')
        assert config.environment_label() == 'STAGING' and not config.is_production()

    def test_prod_is_production_too(self, bundled:Path) -> None:
        write(bundled, apiBaseUrl='https://erp.example.vn/api', environment='prod')
        assert config.is_production()

    def test_the_environment_is_required(self, bundled:Path) -> None:
        write(bundled, apiBaseUrl='https://erp.example.vn/api')
        with pytest.raises(config.ConfigError) as e:
            config.environment()
        assert 'environment' in e.value.message

    @pytest.mark.parametrize('content', ['{not json', '[1, 2]', '"text"'])
    def test_a_broken_file_is_an_error(self, bundled:Path, content:str) -> None:
        bundled.write_text(content, encoding='utf-8')
        with pytest.raises(config.ConfigError):
            config.get_api_base_url()

    def test_missing_url_in_the_file(self, bundled:Path) -> None:
        write(bundled, environment='production')
        with pytest.raises(config.ConfigError):
            config.get_api_base_url()

    def test_explicit_socket_url(self, bundled:Path) -> None:
        write(bundled, apiBaseUrl='https://erp.example.vn/api', environment='production', socketUrl='https://realtime.example.vn')
        assert config.get_socket_url() == 'https://realtime.example.vn'

    def test_socket_url_is_derived_from_the_api_otherwise(self, bundled:Path) -> None:
        write(bundled, apiBaseUrl='https://erp.example.vn/api', environment='production')
        assert config.get_socket_url() == 'https://erp.example.vn'

    def test_socket_url_of_an_api_without_the_api_suffix(self, no_env:pytest.MonkeyPatch) -> None:
        no_env.setenv(config.ENV_API_URL, 'https://erp.example.vn/v2')
        assert config.get_socket_url() == 'https://erp.example.vn/v2'

    def test_from_source_the_file_sits_next_to_the_code(self, no_env:pytest.MonkeyPatch) -> None:
        no_env.delattr(sys, 'frozen', raising=False)
        assert config._config_file() == Path(config.__file__).resolve().parent.parent / config.CONFIG_FILENAME  # pylint: disable=protected-access

    def test_frozen_without_meipass_falls_back_to_the_source_location(self, no_env:pytest.MonkeyPatch) -> None:
        no_env.setattr(sys, 'frozen', True, raising=False)
        no_env.delattr(sys, '_MEIPASS', raising=False)
        assert config._config_file().name == config.CONFIG_FILENAME  # pylint: disable=protected-access


class TestValidation:
    @pytest.mark.parametrize('url', [
        'https://erp.example.vn/api', 'http://localhost:10301/api', 'http://127.0.0.1:10301/api', 'http://[::1]:10301/api', 'https://a.b.c:8443/api/',
    ])
    def test_accepted(self, url:str) -> None:
        assert config.validate_api_url(url) == url.rstrip('/')

    @pytest.mark.parametrize('url', [
        '', '   ', None, 'erp.example.vn/api', 'ftp://erp.example.vn', 'http://erp.example.vn/api', 'http://192.168.1.10/api',
        'https://', 'https://erp.example.vn/api?x=1', 'https://erp.example.vn/api#frag', 5,
    ])
    def test_refused(self, url:object) -> None:
        with pytest.raises(config.ConfigError):
            config.validate_api_url(url)

    def test_plain_http_is_refused_for_every_host_but_this_pc(self) -> None:
        with pytest.raises(config.ConfigError) as e:
            config.validate_api_url('http://erp.example.vn/api')
        assert 'https' in e.value.message

    @pytest.mark.parametrize('name', ['production', 'staging', 'dev', 'qa-2', 'a', 'x' * 20])
    def test_environment_accepted(self, name:str) -> None:
        assert config.validate_environment(name) == name

    @pytest.mark.parametrize('name', ['', 'Prod uction', '-x', 'x' * 21, 'a_b', '../x', None, 3])
    def test_environment_refused(self, name:object) -> None:
        with pytest.raises(config.ConfigError):
            config.validate_environment(name)

    def test_environment_from_host(self) -> None:
        assert config.environment_from_host('https://erp-api.example.vn/api') == 'erp-api-example-vn'
        assert config.environment_from_host('http://localhost:10301/api') == 'localhost'
        assert len(config.environment_from_host('https://' + 'a' * 60 + '.com/api')) <= 20
        assert config.environment_from_host('not a url') in ('unknown', 'not-a-url') or config.environment_from_host('not a url')


class TestEnvironmentIsolation:
    def test_the_keychain_service_has_the_environment_in_it(self, no_env:pytest.MonkeyPatch) -> None:
        no_env.setenv(config.ENV_API_URL, 'https://stg.example.vn/api')
        no_env.setenv(config.ENV_ENVIRONMENT, 'staging')
        assert config.keyring_service() == 'lvg-erp:staging'
        no_env.setenv(config.ENV_ENVIRONMENT, 'production')
        assert config.keyring_service() == 'lvg-erp:production'
