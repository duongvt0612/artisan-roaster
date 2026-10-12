"""Unit tests for erp.roasters: the /api/roasters client (HTTP is mocked, nothing here talks to a server)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock, patch

import pytest

from erp import client, config, roasters


def _resp(status:int, body:Any = None) -> Mock:
    r = Mock()
    r.status_code = status
    if body is None:
        r.json.side_effect = ValueError('no json')
    else:
        r.json.return_value = body
    return r


def _ok(data:Any) -> dict[str, Any]:
    return {'success': True, 'errors': [], 'errorMessages': [], 'data': data}


def _fail(code:str, message:str = 'x') -> dict[str, Any]:
    return {'success': False, 'errors': [code], 'errorMessages': [message], 'data': None}


@pytest.fixture
def sess() -> Any:
    with patch.object(roasters, 'session') as s:
        yield s


MACHINE_FREE = {'id': 'eq1', 'code': 'ROAST-01', 'name': 'Máy Rang Nhỏ', 'capacity': '15.00', 'capacityUnit': 'kg',
                'status': 'free', 'heldBy': None, 'version': 3, 'mine': False, 'heldBySession': False}
MACHINE_HELD = {'id': 'eq2', 'code': 'ROAST-02', 'name': 'Máy rang to', 'capacity': None, 'capacityUnit': None,
                'status': 'occupied', 'version': 8, 'mine': True, 'heldBySession': False,
                'heldBy': {'userId': 'u1', 'fullName': 'Nguyễn A', 'deviceName': 'PC-1', 'since': '2026-10-12T07:00:00.123456Z'}}


class TestList:
    def test_parses_machines(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok([MACHINE_FREE, MACHINE_HELD]))
        out = roasters.list_roasters('s1')
        args, kwargs = sess.authed_request.call_args
        assert args == ('GET', config.get_roasters_url())
        assert kwargs['params'] == {'sessionId': 's1'}
        assert [m.code for m in out] == ['ROAST-01', 'ROAST-02']
        free, held = out
        assert free.is_free and free.held_by is None and free.capacity == '15.00' and free.capacity_unit == 'kg'
        assert not held.is_free and held.mine and not held.held_by_session
        assert held.held_by is not None
        assert (held.held_by.full_name, held.held_by.device_name) == ('Nguyễn A', 'PC-1')
        assert held.held_by.since == datetime(2026, 10, 12, 7, 0, 0, 123456, tzinfo=UTC)

    def test_forbidden_means_no_permission(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(403, _fail('Forbidden', 'Bạn không có quyền thực hiện thao tác này.'))
        with pytest.raises(roasters.RoasterForbidden) as exc:
            roasters.list_roasters('s1')
        assert 'quyền' in exc.value.message

    def test_empty_list(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok([]))
        assert roasters.list_roasters('s1') == []

    def test_malformed_reply_is_a_server_error(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'not': 'a list'}))
        with pytest.raises(roasters.RoasterUnavailable):
            roasters.list_roasters('s1')

    def test_one_broken_item_does_not_hide_the_others(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok([{'id': None}, MACHINE_FREE]))
        assert [m.code for m in roasters.list_roasters('s1')] == ['ROAST-01']


class TestClaim:
    def test_success(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({
            'equipmentId': 'eq1', 'outcome': 'claimed', 'version': 4,
            'leaseExpiresAt': '2026-10-12T08:00:30Z', 'ttlSeconds': 30, 'heartbeatSeconds': 10}))
        c = roasters.claim('eq1', session_id='s1', device_id='d1', device_name='PC-1')
        args, kwargs = sess.authed_request.call_args
        assert args == ('POST', config.get_roasters_url() + '/eq1/claim')
        assert kwargs['json'] == {'sessionId': 's1', 'deviceId': 'd1', 'deviceName': 'PC-1'}
        assert (c.version, c.ttl_seconds, c.heartbeat_seconds, c.outcome) == (4, 30, 10, 'claimed')
        assert c.expires_at == datetime(2026, 10, 12, 8, 0, 30, tzinfo=UTC)

    def test_occupied(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(409, _fail('ROASTER_OCCUPIED', 'Máy rang đang được Nguyễn B sử dụng trên PC-9'))
        with pytest.raises(roasters.RoasterOccupied) as exc:
            roasters.claim('eq1', session_id='s1', device_id='d1', device_name='PC-1')
        assert 'Nguyễn B' in exc.value.message

    def test_not_found(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(404, _fail('Not Found', 'Không tìm thấy máy rang'))
        with pytest.raises(roasters.RoasterNotFound):
            roasters.claim('eq1', session_id='s1', device_id='d1', device_name='PC-1')

    def test_missing_fields_in_a_success_reply_are_a_server_error(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'version': 4}))
        with pytest.raises(roasters.RoasterUnavailable):
            roasters.claim('eq1', session_id='s1', device_id='d1', device_name='PC-1')


class TestHeartbeatAndRelease:
    def test_heartbeat_returns_the_new_expiry(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'leaseExpiresAt': '2026-10-12T08:00:30Z'}))
        exp = roasters.heartbeat('eq1', session_id='s1', version=4)
        args, kwargs = sess.authed_request.call_args
        assert args == ('POST', config.get_roasters_url() + '/eq1/heartbeat')
        assert kwargs['json'] == {'sessionId': 's1', 'version': 4}
        assert exp == datetime(2026, 10, 12, 8, 0, 30, tzinfo=UTC)

    def test_lease_lost(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(409, _fail('LEASE_LOST', 'Phiên của bạn không còn giữ máy rang này'))
        with pytest.raises(roasters.LeaseLost):
            roasters.heartbeat('eq1', session_id='s1', version=4)

    def test_release(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'message': 'Đã nhả máy rang'}))
        roasters.release('eq1', session_id='s1', version=4)
        args, kwargs = sess.authed_request.call_args
        assert args == ('POST', config.get_roasters_url() + '/eq1/release')
        assert kwargs['json'] == {'sessionId': 's1', 'version': 4}


class TestForceRelease:
    def test_success_with_reason(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'message': 'Đã ép nhả máy rang'}))
        roasters.force_release('eq1', reason='PC bị treo')
        args, kwargs = sess.authed_request.call_args
        assert args == ('POST', config.get_roasters_url() + '/eq1/force-release')
        assert kwargs['json'] == {'reason': 'PC bị treo'}

    def test_not_held(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(409, _fail('ROASTER_NOT_HELD', 'Máy rang đang trống'))
        with pytest.raises(roasters.NotHeld):
            roasters.force_release('eq1', reason='')


class TestErrorMapping:
    def test_network_error_becomes_unavailable(self, sess:Any) -> None:
        sess.authed_request.side_effect = client.ErpAuthError('network', 'down')
        with pytest.raises(roasters.RoasterUnavailable) as exc:
            roasters.heartbeat('eq1', session_id='s1', version=1)
        assert exc.value.kind == 'network'

    def test_dead_session_becomes_session_expired(self, sess:Any) -> None:
        sess.authed_request.side_effect = client.ErpAuthError('invalid_session', 'expired')
        with pytest.raises(roasters.SessionExpired):
            roasters.list_roasters('s1')

    @pytest.mark.parametrize(('status', 'kind'), [(500, 'server'), (502, 'server'), (429, 'rate_limited')])
    def test_server_and_rate_limit(self, sess:Any, status:int, kind:str) -> None:
        sess.authed_request.return_value = _resp(status, None)
        with pytest.raises(roasters.RoasterUnavailable) as exc:
            roasters.list_roasters('s1')
        assert exc.value.kind == kind

    def test_all_errors_share_a_base_class(self) -> None:
        for cls in (roasters.RoasterOccupied, roasters.LeaseLost, roasters.NotHeld, roasters.RoasterNotFound,
                    roasters.RoasterForbidden, roasters.RoasterUnavailable, roasters.SessionExpired):
            assert issubclass(cls, roasters.RoasterError)
