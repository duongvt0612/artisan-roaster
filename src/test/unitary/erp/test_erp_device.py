"""Unit tests for erp.device: stable PC id, per-run session id and the remembered roaster."""

import uuid
from typing import Any

import pytest

from erp import config, device


class MemStore:
    """Same shape as the QSettings calls device.py uses."""

    def __init__(self) -> None:
        self.data:dict[str, Any] = {}

    def value(self, key:str, default:Any = None) -> Any:
        return self.data.get(key, default)

    def setValue(self, key:str, value:Any) -> None:  # noqa: N802 # QSettings API
        self.data[key] = value


def test_device_id_is_created_once_and_then_stable() -> None:
    store = MemStore()
    first = device.device_id(store)
    uuid.UUID(first)  # a real UUID
    assert device.device_id(store) == first
    assert store.data[device.KEY_DEVICE_ID] == first


def test_device_id_survives_a_new_process() -> None:
    store = MemStore()
    first = device.device_id(store)
    # a "new process" only shares the store
    assert device.device_id(store) == first


def test_corrupt_stored_device_id_is_replaced() -> None:
    store = MemStore()
    store.data[device.KEY_DEVICE_ID] = '   '
    fresh = device.device_id(store)
    uuid.UUID(fresh)
    assert store.data[device.KEY_DEVICE_ID] == fresh


def test_broken_store_still_gives_a_usable_id() -> None:
    class Broken:
        def value(self, *_:Any) -> Any:
            raise OSError('registry locked')

        def setValue(self, *_:Any) -> None:  # noqa: N802
            raise OSError('registry locked')

    first = device.device_id(Broken())
    uuid.UUID(first)


def test_session_id_is_unique_per_process_but_constant_within_it() -> None:
    assert device.session_id() == device.session_id()
    uuid.UUID(device.session_id())


def test_device_name_is_trimmed_and_never_empty() -> None:
    name = device.device_name()
    assert name and len(name) <= 100
    assert name == name.strip()


def test_device_name_is_asked_of_the_system_only_once(monkeypatch:pytest.MonkeyPatch) -> None:
    # it goes into the headers of every request to the ERP
    calls:list[int] = []

    def hostname() -> str:
        calls.append(1)
        return 'Máy-rang-1'

    monkeypatch.setattr(device.socket, 'gethostname', hostname)
    device.device_name.cache_clear()
    try:
        assert device.device_name() == 'Máy-rang-1'
        assert device.device_name() == 'Máy-rang-1'
        assert len(calls) == 1
    finally:
        device.device_name.cache_clear()


def test_last_roaster_roundtrip() -> None:
    store = MemStore()
    assert device.last_roaster_id(store) is None
    device.set_last_roaster_id('eq-1', store)
    assert device.last_roaster_id(store) == 'eq-1'
    device.set_last_roaster_id(None, store)
    assert device.last_roaster_id(store) is None


def test_the_remembered_roaster_is_kept_per_environment(monkeypatch:pytest.MonkeyPatch) -> None:
    store = MemStore()
    monkeypatch.setenv(config.ENV_ENVIRONMENT, 'staging')
    config.reset_cache()
    device.set_last_roaster_id('eq-staging', store)
    monkeypatch.setenv(config.ENV_ENVIRONMENT, 'production')
    config.reset_cache()
    assert device.last_roaster_id(store) is None            # a roaster of the test server means nothing here
    device.set_last_roaster_id('eq-prod', store)
    assert device.last_roaster_id(store) == 'eq-prod'
    monkeypatch.setenv(config.ENV_ENVIRONMENT, 'staging')
    config.reset_cache()
    assert device.last_roaster_id(store) == 'eq-staging'


def test_the_pc_keeps_one_device_id_for_every_environment(monkeypatch:pytest.MonkeyPatch) -> None:
    store = MemStore()
    monkeypatch.setenv(config.ENV_ENVIRONMENT, 'staging')
    config.reset_cache()
    first = device.device_id(store)
    monkeypatch.setenv(config.ENV_ENVIRONMENT, 'production')
    config.reset_cache()
    assert device.device_id(store) == first


def test_without_a_server_the_key_is_still_defined(monkeypatch:pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config.ENV_API_URL, raising=False)
    monkeypatch.delenv(config.ENV_ENVIRONMENT, raising=False)
    monkeypatch.setattr(config, '_config_file', lambda: __import__('pathlib').Path('/nonexistent/erp-config.json'))
    config.reset_cache()
    assert device.last_roaster_key().endswith('.unconfigured')
