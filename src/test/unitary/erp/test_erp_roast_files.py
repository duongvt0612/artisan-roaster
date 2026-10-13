"""Unit tests for erp.roast_files: the .alog and PDF of a finished batch go into the upload queue (fake Artisan window, temporary directory)."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from erp import roast_files
from erp.attachments import UploadQueue
from erp.batch import Batch


def a_batch(**kw:Any) -> Batch:
    base = {'round_id': 'r1', 'round_number': 3, 'command_id': 'c1', 'command_code': 'LS-1026-000123', 'kind': 'normal',
            'output_name': 'Arabica rang', 'output_unit': 'kg', 'input_kg': 40.0, 'expected_output_kg': 32.0, 'tolerance_percent': 15.0,
            'started_at': '', 'equipment_id': 'eq1'}
    base.update(kw)
    return Batch(**base)


class FakeAw:
    def __init__(self, round_id:str = 'r1', pdf:bytes|None = b'%PDF-1.7 report') -> None:
        self.profile:dict[str, Any] = {'version': '4.0', 'erpRoundId': round_id, 'erpCommandCode': 'LS-1026-000123', 'erpRoundNumber': 3}
        self.profile_error:Exception|None = None
        self.pdf = pdf
        self.pdf_error:Exception|None = None
        self.report_calls:list[str] = []

    def getProfile(self, copy:bool = False, generate_hash:bool = False) -> dict[str, Any]:  # noqa: N802, ARG002
        if self.profile_error is not None:
            raise self.profile_error
        return self.profile

    def roastReport(self, pdf_filename:str|None = None) -> None:  # noqa: N802
        assert pdf_filename is not None
        self.report_calls.append(pdf_filename)
        if self.pdf_error is not None:
            raise self.pdf_error
        if self.pdf is not None:
            Path(pdf_filename).write_bytes(self.pdf)


@pytest.fixture
def queue(tmp_path:Path) -> UploadQueue:
    return UploadQueue(str(tmp_path / 'q'))


class TestFileStem:
    def test_names_the_batch(self) -> None:
        assert roast_files.file_stem(a_batch()) == 'LS-1026-000123-m3'

    def test_unsafe_characters_become_underscores(self) -> None:
        assert roast_files.file_stem(a_batch(command_code='LS 12/3:ñ')) == 'LS_12_3_-m3'
        assert roast_files.file_stem(a_batch(command_code='../..')) == 'm3'


class TestBuild:
    def test_alog_is_the_profile_literal_with_the_batch_link(self) -> None:
        aw = FakeAw()
        content = roast_files.build_alog(aw, a_batch())  # type:ignore[arg-type]
        assert content is not None
        assert content == repr(aw.profile).encode('utf-8')
        assert b"'erpRoundId': 'r1'" in content   # what the ERP looks for

    def test_a_profile_of_another_recording_is_not_sent(self) -> None:
        assert roast_files.build_alog(FakeAw(round_id='other'), a_batch()) is None  # type:ignore[arg-type]
        aw = FakeAw()
        aw.profile = {'version': '4.0'}
        assert roast_files.build_alog(aw, a_batch()) is None  # type:ignore[arg-type]
        aw.profile = {}
        assert roast_files.build_alog(aw, a_batch()) is None  # type:ignore[arg-type]

    def test_profile_failure_is_swallowed(self) -> None:
        aw = FakeAw()
        aw.profile_error = RuntimeError('boom')
        assert roast_files.build_alog(aw, a_batch()) is None  # type:ignore[arg-type]

    def test_pdf_is_read_and_the_temporary_file_removed(self) -> None:
        aw = FakeAw()
        content = roast_files.build_pdf(aw)  # type:ignore[arg-type]
        assert content == b'%PDF-1.7 report'
        assert len(aw.report_calls) == 1 and not os.path.exists(aw.report_calls[0])

    def test_the_busy_callback_brackets_the_report_even_when_it_fails(self) -> None:
        aw = FakeAw()
        aw.pdf_error = RuntimeError('boom')
        busy = MagicMock()
        assert roast_files.build_pdf(aw, busy=busy) is None  # type:ignore[arg-type]
        assert [c.args[0] for c in busy.call_args_list] == [True, False]
        assert not os.path.exists(aw.report_calls[0])

    @pytest.mark.parametrize('pdf', [None, b'', b'<html>not a pdf</html>'])
    def test_a_missing_or_wrong_report_is_not_sent(self, pdf:bytes|None) -> None:
        assert roast_files.build_pdf(FakeAw(pdf=pdf)) is None  # type:ignore[arg-type]

    def test_without_the_pdf_engine_no_report_is_attempted(self) -> None:
        aw = FakeAw()
        with patch.object(roast_files.importlib.util, 'find_spec', return_value=None):
            assert roast_files.build_pdf(aw) is None  # type:ignore[arg-type]
        assert aw.report_calls == []


class TestEnqueue:
    def test_queues_the_alog_and_the_pdf_under_the_batch_name(self, queue:UploadQueue) -> None:
        jobs = roast_files.build_and_enqueue(FakeAw(), a_batch(), queue)  # type:ignore[arg-type]
        assert [(j.kind, j.filename, j.round_id) for j in jobs] == [('alog', 'LS-1026-000123-m3.alog', 'r1'), ('pdf', 'LS-1026-000123-m3.pdf', 'r1')]
        assert queue.pending_count() == 2
        assert queue.read(jobs[1]) == b'%PDF-1.7 report'

    def test_a_failing_report_still_sends_the_alog(self, queue:UploadQueue) -> None:
        aw = FakeAw()
        aw.pdf_error = RuntimeError('boom')
        jobs = roast_files.build_and_enqueue(aw, a_batch(), queue)  # type:ignore[arg-type]
        assert [j.kind for j in jobs] == ['alog']

    def test_a_recording_that_is_not_the_batch_queues_nothing_and_makes_no_report(self, queue:UploadQueue) -> None:
        aw = FakeAw(round_id='other')
        assert roast_files.build_and_enqueue(aw, a_batch(), queue) == []  # type:ignore[arg-type]
        assert aw.report_calls == [] and queue.pending_count() == 0

    def test_the_pdf_can_be_left_out(self, queue:UploadQueue) -> None:
        aw = FakeAw()
        jobs = roast_files.build_and_enqueue(aw, a_batch(), queue, pdf=False)  # type:ignore[arg-type]
        assert [j.kind for j in jobs] == ['alog'] and aw.report_calls == []

    def test_a_second_build_replaces_the_files_of_the_same_batch(self, queue:UploadQueue) -> None:
        roast_files.build_and_enqueue(FakeAw(), a_batch(), queue)  # type:ignore[arg-type]
        roast_files.build_and_enqueue(FakeAw(pdf=b'%PDF-1.7 newer'), a_batch(), queue)  # type:ignore[arg-type]
        assert queue.pending_count() == 2
        assert queue.read([j for j in queue.pending() if j.kind == 'pdf'][0]) == b'%PDF-1.7 newer'
