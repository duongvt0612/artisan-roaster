"""Unit tests for erp.rounds: the /api/artisan roasting client (HTTP is mocked, nothing here talks to a server)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock, patch

import pytest

from erp import config, roasters, rounds


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


COMMAND = {
    'commandId': 'c1', 'code': 'LS-1026-000123', 'kind': 'normal', 'status': 'in_progress', 'stepNumber': 1,
    'output': {'inventoryId': 'o1', 'code': 'TP-ARA', 'name': 'Arabica Cầu Đất rang', 'unit': 'kg'},
    'targetQuantity': 120, 'producedQuantity': 40, 'remainingQuantity': 80,
    'source': {'requestCode': 'YC-9', 'saleOrders': [{'orderNumber': 'SO-7', 'customerName': 'Khách A'}]},
    'inputs': [{'inventoryId': 'g1', 'code': 'NX-A', 'name': 'Nhân A', 'unit': 'kg', 'availableKg': 55.5}],
}


class TestSearch:
    def test_parses_what_the_operator_must_verify(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok([COMMAND]))
        out = rounds.search_commands('  arabica ', 5)
        args, kwargs = sess.authed_request.call_args
        assert args == ('GET', config.get_artisan_url() + '/roasting-commands')
        assert kwargs['params'] == {'q': 'arabica', 'limit': 5}
        assert len(out) == 1
        c = out[0]
        assert (c.code, c.output_name, c.output_unit, c.target_kg, c.produced_kg, c.remaining_kg) == ('LS-1026-000123', 'Arabica Cầu Đất rang', 'kg', 120, 40, 80)
        assert c.in_progress and not c.is_test
        assert c.request_code == 'YC-9' and c.sale_orders == (rounds.SaleOrder('SO-7', 'Khách A'),)
        assert c.inputs == (rounds.RoundInput('g1', 'NX-A', 'Nhân A', 'kg', 55.5),)

    def test_test_kind(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok([{**COMMAND, 'kind': 'test'}]))
        assert rounds.search_commands('')[0].is_test

    def test_a_broken_item_does_not_hide_the_others(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok([{'code': 'x'}, COMMAND]))
        assert [c.code for c in rounds.search_commands('')] == ['LS-1026-000123']

    def test_missing_optional_parts(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok([{'commandId': 'c2', 'code': 'LS-2', 'stepNumber': 1}]))
        c = rounds.search_commands('')[0]
        assert c.inputs == () and c.sale_orders == () and c.output_unit == 'kg' and c.kind == 'normal'

    def test_not_a_list_is_a_server_error(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'x': 1}))
        with pytest.raises(roasters.RoasterUnavailable):
            rounds.search_commands('')


class TestStart:
    def _call(self, sess:Any, response:Mock) -> rounds.StartedRound:
        sess.authed_request.return_value = response
        return rounds.start_round(equipment_id='eq1', session_id='s1', version=4, command_id='c1', step_number=1,
                                  inputs=[('g1', 12.5)], notes='mẻ sáng')

    def test_sends_the_lease_and_the_inputs(self, sess:Any) -> None:
        out = self._call(sess, _resp(201, _ok({'roundId': 'r1', 'roundNumber': 3, 'commandId': 'c1', 'stepNumber': 1,
                                              'expectedOutputQuantity': 10.2, 'tolerancePercentage': 15, 'startedAt': '2026-10-13T08:00:00.000Z'})))
        args, kwargs = sess.authed_request.call_args
        assert args == ('POST', config.get_artisan_url() + '/rounds/start')
        assert kwargs['json'] == {'equipmentId': 'eq1', 'sessionId': 's1', 'version': 4, 'commandId': 'c1', 'stepNumber': 1,
                                  'inputs': [{'inventoryId': 'g1', 'quantity': 12.5}], 'notes': 'mẻ sáng'}
        assert out == rounds.StartedRound('r1', 3, 'c1', 1, 10.2, 15.0, datetime(2026, 10, 13, 8, 0, tzinfo=UTC))

    @pytest.mark.parametrize(('code', 'exc'), [
        ('LEASE_LOST', roasters.LeaseLost), ('ROASTER_BUSY', rounds.RoasterBusy), ('QUOTA_EXCEEDED', rounds.QuotaExceeded),
        ('COMMAND_NOT_ROASTABLE', rounds.NotRoastable),
    ])
    def test_conflict_codes(self, sess:Any, code:str, exc:type[Exception]) -> None:
        with pytest.raises(exc) as e:
            self._call(sess, _resp(409, _fail(code, 'lý do')))
        assert e.value.message == 'lý do'  # type: ignore[attr-defined]

    def test_other_errors(self, sess:Any) -> None:
        with pytest.raises(roasters.SessionExpired):
            self._call(sess, _resp(401, _fail('Unauthorized')))
        with pytest.raises(roasters.RoasterForbidden):
            self._call(sess, _resp(403, _fail('Forbidden')))
        with pytest.raises(roasters.RoasterNotFound):
            self._call(sess, _resp(404, _fail('Not Found')))
        with pytest.raises(roasters.RoasterUnavailable):
            self._call(sess, _resp(503, _fail('x')))
        with pytest.raises(roasters.RoasterError):
            self._call(sess, _resp(400, _fail('Bad Request', 'sai')))

    def test_malformed_reply(self, sess:Any) -> None:
        with pytest.raises(roasters.RoasterUnavailable):
            self._call(sess, _resp(201, _ok({'roundNumber': 1})))


class TestFinish:
    def test_sends_weight_color_and_notes(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'roundId': 'r1', 'roundNumber': 3, 'outputQuantity': 30, 'expectedOutputQuantity': 32,
                                                          'lossRate': 25.0, 'lossExceeded': True, 'tolerancePercentage': 15}))
        out = rounds.finish_round('r1', output_kg=30, powder_color='#85', notes='ok')
        args, kwargs = sess.authed_request.call_args
        assert args == ('POST', config.get_artisan_url() + '/rounds/r1/complete')
        assert kwargs['json'] == {'outputQuantity': 30, 'notes': 'ok', 'powderColor': '#85'}
        assert out == rounds.FinishedRound('r1', 3, 30, 32, 25.0, True, 15.0)

    def test_optional_fields_are_left_out(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'roundId': 'r1'}))
        rounds.finish_round('r1', output_kg=1)
        assert sess.authed_request.call_args.kwargs['json'] == {'outputQuantity': 1}

    def test_not_running(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(409, _fail('ROUND_NOT_RUNNING', 'xong rồi'))
        with pytest.raises(rounds.RoundNotRunning):
            rounds.finish_round('r1', output_kg=1)

    def test_somebody_elses_batch_is_forbidden(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(403, _fail('Forbidden', 'Mẻ này do người khác bắt đầu'))
        with pytest.raises(roasters.RoasterForbidden):
            rounds.finish_round('r1', output_kg=1)


class TestCancel:
    def test_sends_the_reason(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'roundId': 'r1', 'message': 'ok'}))
        rounds.cancel_round('r1', reason='rang hỏng')
        args, kwargs = sess.authed_request.call_args
        assert args == ('POST', config.get_artisan_url() + '/rounds/r1/cancel')
        assert kwargs['json'] == {'reason': 'rang hỏng'}

    def test_not_running(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(409, _fail('ROUND_NOT_RUNNING'))
        with pytest.raises(rounds.RoundNotRunning):
            rounds.cancel_round('r1', reason='x')


class TestActive:
    def test_none_when_the_roaster_is_free(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok(None))
        assert rounds.active_round('eq1') is None
        assert sess.authed_request.call_args.args == ('GET', config.get_artisan_url() + '/roasters/eq1/active-round')

    def test_parses_the_running_batch(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({
            'roundId': 'r1', 'roundNumber': 2, 'commandId': 'c1', 'commandCode': 'LS-1', 'kind': 'test', 'stepNumber': 1,
            'output': {'code': 'TP', 'name': 'Thành phẩm', 'unit': 'kg'}, 'targetQuantity': 100, 'expectedOutputQuantity': 8.5,
            'tolerancePercentage': 12, 'startedAt': '2026-10-13T07:15:00.000Z', 'startedBy': {'userId': 'u1', 'fullName': 'Thợ rang'},
            'fromArtisan': True, 'mine': True,
            'inputs': [{'inventoryId': 'g1', 'name': 'Nhân A', 'unit': 'kg', 'availableKg': 6}, {'inventoryId': 'g2', 'name': 'Nhân B', 'unit': 'kg', 'availableKg': 4.5}],
        }))
        a = rounds.active_round('eq1')
        assert a is not None
        assert (a.round_id, a.round_number, a.command_code, a.kind, a.output_name, a.target_kg, a.expected_output_kg) == ('r1', 2, 'LS-1', 'test', 'Thành phẩm', 100, 8.5)
        assert (a.started_by_id, a.started_by_name, a.from_artisan, a.mine, a.tolerance_percent) == ('u1', 'Thợ rang', True, True, 12)
        assert a.started_at == datetime(2026, 10, 13, 7, 15, tzinfo=UTC)
        assert a.input_kg == 10.5


class TestUploadFile:
    def _upload(self, sess:Any, response:Mock) -> rounds.UploadedFile:
        sess.authed_request.return_value = response
        return rounds.upload_file('r1', kind='pdf', filename='mẻ 3.pdf', content=b'%PDF-1.7 x', sha256='ab' * 32)

    def test_sends_a_multipart_with_the_checksum(self, sess:Any) -> None:
        out = self._upload(sess, _resp(200, _ok({'id': 'f1', 'kind': 'pdf', 'sizeBytes': 10})))
        args, kwargs = sess.authed_request.call_args
        assert args == ('POST', config.get_artisan_url() + '/rounds/r1/files')
        assert kwargs['data'] == {'kind': 'pdf', 'sha256': 'ab' * 32}
        assert kwargs['files'] == {'file': ('mẻ 3.pdf', b'%PDF-1.7 x', 'application/pdf')}
        assert 'json' not in kwargs or kwargs['json'] is None
        assert out == rounds.UploadedFile('f1', 'pdf', 10)

    def test_alog_content_type(self, sess:Any) -> None:
        sess.authed_request.return_value = _resp(200, _ok({'id': 'f2'}))
        rounds.upload_file('r1', kind='alog', filename='a.alog', content=b"{'a': 1}", sha256='cd' * 32)
        assert sess.authed_request.call_args.kwargs['files']['file'][2] == 'application/octet-stream'

    def test_a_later_try_is_worth_it_when_the_round_is_not_completed_yet(self, sess:Any) -> None:
        with pytest.raises(rounds.RoundNotCompleted):
            self._upload(sess, _resp(409, _fail('ROUND_NOT_COMPLETED', 'chưa xong')))

    @pytest.mark.parametrize('code', ['FILE_INVALID', 'FILE_ROUND_MISMATCH'])
    def test_a_refused_file_is_never_worth_another_try(self, sess:Any, code:str) -> None:
        with pytest.raises(rounds.FileRejected) as e:
            self._upload(sess, _resp(400, _fail(code, 'sai')))
        assert e.value.message == 'sai'  # type: ignore[attr-defined]

    def test_other_errors(self, sess:Any) -> None:
        with pytest.raises(roasters.SessionExpired):
            self._upload(sess, _resp(401, _fail('Unauthorized')))
        with pytest.raises(roasters.RoasterForbidden):
            self._upload(sess, _resp(403, _fail('Forbidden')))
        with pytest.raises(roasters.RoasterNotFound):
            self._upload(sess, _resp(404, _fail('Not Found')))
        with pytest.raises(roasters.RoasterUnavailable):
            self._upload(sess, _resp(503, _fail('x')))
        with pytest.raises(roasters.RoasterUnavailable):
            self._upload(sess, _resp(429, _fail('x')))
        with pytest.raises(roasters.RoasterError) as e:
            self._upload(sess, _resp(400, _fail('Bad Request', 'thiếu field')))
        assert not isinstance(e.value, rounds.FileRejected)

    def test_malformed_reply(self, sess:Any) -> None:
        with pytest.raises(roasters.RoasterUnavailable):
            self._upload(sess, _resp(200, _ok({'kind': 'pdf'})))
