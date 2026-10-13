"""Every erp test runs against a fixed fake ERP server: the code has no default server (see erp.config), so one is set here."""

from collections.abc import Generator

import pytest

from erp import config


@pytest.fixture(autouse=True)
def _erp_server(monkeypatch:pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv(config.ENV_API_URL, 'https://erp.test/api')
    monkeypatch.setenv(config.ENV_ENVIRONMENT, 'production')
    monkeypatch.delenv(config.ENV_SOCKET_URL, raising=False)
    config.reset_cache()
    yield
    config.reset_cache()
