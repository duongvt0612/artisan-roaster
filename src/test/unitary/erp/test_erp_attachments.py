"""Unit tests for erp.attachments: the durable upload queue and the background sender (fake ERP API, fake clock, temporary directory)."""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from erp import attachments, roasters, rounds
from erp.attachments import Job, Uploader, UploadQueue


class Clock:
    def __init__(self, now:float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeApi:
    def __init__(self) -> None:
        self.calls:list[dict[str, Any]] = []
        self.errors:list[Exception|None] = []  # popped one per call; None = success

    def upload_file(self, round_id:str, *, kind:str, filename:str, content:bytes, sha256:str) -> rounds.UploadedFile:
        self.calls.append({'round_id': round_id, 'kind': kind, 'filename': filename, 'content': content, 'sha256': sha256})
        err = self.errors.pop(0) if self.errors else None
        if err is not None:
            raise err
        return rounds.UploadedFile('f1', kind, len(content))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def queue(tmp_path:Path, clock:Clock) -> UploadQueue:
    return UploadQueue(str(tmp_path / 'q'), clock=clock)


def names(q:UploadQueue) -> list[str]:
    return sorted(os.listdir(q.directory)) if os.path.isdir(q.directory) else []


class TestBackoff:
    def test_doubles_from_thirty_seconds_up_to_ten_minutes(self) -> None:
        assert [attachments.backoff_seconds(n) for n in (1, 2, 3, 4, 5, 6, 7, 20)] == [30, 60, 120, 240, 480, 600, 600, 600]

    def test_zero_attempts_is_the_first_delay(self) -> None:
        assert attachments.backoff_seconds(0) == 30


class TestQueue:
    def test_add_keeps_the_content_and_describes_it(self, queue:UploadQueue) -> None:
        job = queue.add('r1', 'pdf', 'mẻ 3.pdf', b'%PDF-1.7 x')
        assert (job.round_id, job.kind, job.filename, job.size_bytes, job.attempts) == ('r1', 'pdf', 'mẻ 3.pdf', 10, 0)
        assert len(job.sha256) == 64
        assert queue.read(job) == b'%PDF-1.7 x'
        assert queue.pending_count() == 1
        assert [j.id for j in queue.pending()] == [job.id]

    def test_a_newer_file_of_the_same_batch_and_kind_replaces_the_waiting_one(self, queue:UploadQueue) -> None:
        old = queue.add('r1', 'alog', 'a.alog', b"{'v': 1}")
        new = queue.add('r1', 'alog', 'a.alog', b"{'v': 2}")
        other_kind = queue.add('r1', 'pdf', 'a.pdf', b'%PDF-')
        other_round = queue.add('r2', 'alog', 'b.alog', b"{'v': 3}")
        ids = {j.id for j in queue.pending()}
        assert ids == {new.id, other_kind.id, other_round.id} and old.id not in ids
        assert not any(n.startswith(old.id) for n in names(queue))

    def test_unknown_kind_is_refused(self, queue:UploadQueue) -> None:
        with pytest.raises(ValueError):
            queue.add('r1', 'exe', 'x', b'x')

    def test_survives_a_restart(self, queue:UploadQueue, clock:Clock) -> None:
        job = queue.add('r1', 'pdf', 'a.pdf', b'%PDF-')
        again = UploadQueue(queue.directory, clock=clock)  # a new process looking at the same directory
        assert [j.id for j in again.pending()] == [job.id] and again.read(job) == b'%PDF-'

    def test_oldest_first(self, queue:UploadQueue, clock:Clock) -> None:
        a = queue.add('r1', 'pdf', 'a', b'%PDF-')
        clock.now += 5
        b = queue.add('r2', 'pdf', 'b', b'%PDF-')
        assert [j.id for j in queue.pending()] == [a.id, b.id]

    def test_retry_later_counts_and_delays(self, queue:UploadQueue, clock:Clock) -> None:
        job = queue.add('r1', 'pdf', 'a', b'%PDF-')
        assert [j.id for j in queue.due()] == [job.id]
        queue.retry_later(job, 'network')
        assert queue.due() == []
        stored = queue.pending()[0]
        assert (stored.attempts, stored.last_error, stored.next_try_at) == (1, 'network', clock.now + 30)
        clock.now += 31
        assert [j.id for j in queue.due()] == [job.id]

    def test_retry_now_makes_everything_due(self, queue:UploadQueue) -> None:
        job = queue.add('r1', 'pdf', 'a', b'%PDF-')
        queue.retry_later(job, 'x')
        queue.retry_now()
        assert [j.id for j in queue.due()] == [job.id]

    def test_done_removes_both_files(self, queue:UploadQueue) -> None:
        job = queue.add('r1', 'pdf', 'a', b'%PDF-')
        queue.done(job)
        queue.done(job)  # twice is fine
        assert names(queue) == [] and queue.pending_count() == 0

    def test_reject_keeps_the_file_and_the_reason(self, queue:UploadQueue) -> None:
        job = queue.add('r1', 'pdf', 'a.pdf', b'%PDF-')
        queue.reject(job, 'FILE_INVALID')
        assert queue.pending_count() == 0
        rejected = sorted(os.listdir(queue.rejected_directory))
        assert rejected == [f'{job.id}.bin', f'{job.id}.json', f'{job.id}.reason.json']
        reason = json.loads(Path(queue.rejected_directory, f'{job.id}.reason.json').read_text(encoding='utf-8'))
        assert reason['reason'] == 'FILE_INVALID' and reason['round_id'] == 'r1'
        assert Path(queue.rejected_directory, f'{job.id}.bin').read_bytes() == b'%PDF-'

    def test_an_unreadable_description_is_set_aside_not_retried_forever(self, queue:UploadQueue) -> None:
        os.makedirs(queue.directory)
        Path(queue.directory, 'bad.json').write_text('{not json', encoding='utf-8')
        Path(queue.directory, 'bad.bin').write_bytes(b'x')
        assert queue.pending() == []
        assert 'bad.json' not in names(queue) and os.path.exists(os.path.join(queue.rejected_directory, 'bad.json'))

    def test_cleanup_removes_what_a_crash_left_behind(self, queue:UploadQueue) -> None:
        job = queue.add('r1', 'pdf', 'a', b'%PDF-')
        Path(queue.directory, 'x.json.1234.tmp').write_bytes(b'half')
        Path(queue.directory, 'orphan.bin').write_bytes(b'no description')
        queue.cleanup_orphans()
        assert sorted(names(queue)) == [f'{job.id}.bin', f'{job.id}.json']

    def test_a_job_does_not_exist_until_its_description_is_written(self, queue:UploadQueue, monkeypatch:pytest.MonkeyPatch) -> None:
        def boom(_job:Job) -> None:
            raise OSError('disk full')

        monkeypatch.setattr(queue, '_save_meta', boom)
        with pytest.raises(OSError):
            queue.add('r1', 'pdf', 'a', b'%PDF-')
        assert queue.pending() == []        # no half written job is ever picked up
        queue.cleanup_orphans()
        assert [n for n in names(queue) if not n.endswith('.json')] == []   # and the orphan content is cleaned

    def test_default_directory_is_under_the_data_directory_and_the_environment(self) -> None:
        assert attachments.default_directory().endswith(os.path.join(attachments.QUEUE_DIR_NAME, 'production'))

    def test_each_environment_has_a_queue_of_its_own(self, monkeypatch:pytest.MonkeyPatch) -> None:
        from erp import config
        monkeypatch.setenv(config.ENV_ENVIRONMENT, 'staging')
        config.reset_cache()
        staging = attachments.default_directory()
        monkeypatch.setenv(config.ENV_ENVIRONMENT, 'production')
        config.reset_cache()
        production = attachments.default_directory()
        assert staging != production and os.path.dirname(staging) == os.path.dirname(production)   # same parent, different folder
        assert staging.endswith('staging') and production.endswith('production')


def uploader(queue:UploadQueue, api:FakeApi) -> tuple[Uploader, list[tuple[str, Job|None, str]]]:
    events:list[tuple[str, Job|None, str]] = []
    u = Uploader(queue, api=api, interval=0.05)
    u.add_listener(lambda e, j, r: events.append((e, j, r)))
    return u, events


class TestUploader:
    def test_sends_what_is_queued_and_forgets_it(self, queue:UploadQueue) -> None:
        job = queue.add('r1', 'pdf', 'mẻ 3.pdf', b'%PDF-1.7 x')
        api = FakeApi()
        u, events = uploader(queue, api)
        assert u.run_once() == 1
        assert api.calls == [{'round_id': 'r1', 'kind': 'pdf', 'filename': 'mẻ 3.pdf', 'content': b'%PDF-1.7 x', 'sha256': job.sha256}]
        assert queue.pending_count() == 0 and names(queue) == []
        assert [e for e, _j, _r in events] == ['sent', 'changed']

    def test_sends_oldest_first(self, queue:UploadQueue, clock:Clock) -> None:
        queue.add('r1', 'pdf', 'a', b'%PDF-')
        clock.now += 1
        queue.add('r2', 'pdf', 'b', b'%PDF-')
        api = FakeApi()
        uploader(queue, api)[0].run_once()
        assert [c['round_id'] for c in api.calls] == ['r1', 'r2']

    @pytest.mark.parametrize('error', [
        roasters.RoasterUnavailable('network', 'down'), roasters.RoasterUnavailable('server', 'busy'),
        roasters.RoasterUnavailable('rate_limited', 'slow'), rounds.RoundNotCompleted('chưa xong'), RuntimeError('bug'),
    ])
    def test_temporary_trouble_keeps_the_file_and_tries_again_later(self, queue:UploadQueue, clock:Clock, error:Exception) -> None:
        queue.add('r1', 'pdf', 'a', b'%PDF-')
        api = FakeApi()
        api.errors = [error]
        u, events = uploader(queue, api)
        assert u.run_once() == 0
        job = queue.pending()[0]
        assert job.attempts == 1 and job.next_try_at == clock.now + 30
        assert events == []                     # nothing to tell the user yet
        assert u.run_once() == 0 and len(api.calls) == 1   # not due: no second call
        clock.now += 31
        assert u.run_once() == 1 and queue.pending_count() == 0

    def test_the_delay_grows(self, queue:UploadQueue, clock:Clock) -> None:
        queue.add('r1', 'pdf', 'a', b'%PDF-')
        api = FakeApi()
        api.errors = [roasters.RoasterUnavailable('network', 'x')] * 4
        u, _ = uploader(queue, api)
        delays = []
        for _ in range(4):
            u.run_once()
            delays.append(queue.pending()[0].next_try_at - clock.now)
            clock.now += 1000
        assert delays == [30, 60, 120, 240]

    @pytest.mark.parametrize('error', [
        rounds.FileRejected('sai nội dung'), roasters.RoasterForbidden('không phải của bạn'), roasters.RoasterNotFound('không có mẻ'),
        roasters.RoasterError('400 lạ'),
    ])
    def test_a_refused_file_is_never_retried_and_the_user_is_told(self, queue:UploadQueue, error:roasters.RoasterError) -> None:
        job = queue.add('r1', 'pdf', 'a', b'%PDF-')
        api = FakeApi()
        api.errors = [error]
        u, events = uploader(queue, api)
        assert u.run_once() == 0
        assert queue.pending_count() == 0 and os.path.exists(os.path.join(queue.rejected_directory, f'{job.id}.bin'))
        assert events[0][0] == 'rejected' and events[0][1] is not None and events[0][2] == error.message
        assert u.run_once() == 0 and len(api.calls) == 1

    def test_an_expired_login_pauses_everything_until_resumed(self, queue:UploadQueue) -> None:
        queue.add('r1', 'pdf', 'a', b'%PDF-')
        queue.add('r2', 'pdf', 'b', b'%PDF-')
        api = FakeApi()
        api.errors = [roasters.SessionExpired('hết hạn')]
        u, events = uploader(queue, api)
        assert u.run_once() == 0
        assert u.paused and len(api.calls) == 1 and queue.pending_count() == 2   # the second was not even tried
        assert queue.pending()[0].attempts == 0      # and no delay was set: it is not the file's fault
        assert u.run_once() == 0 and len(api.calls) == 1
        u.resume()
        assert not u.paused
        assert u.run_once() == 2 and queue.pending_count() == 0
        assert events and all(e != 'rejected' for e, _j, _r in events)

    def test_a_missing_content_file_is_set_aside(self, queue:UploadQueue) -> None:
        job = queue.add('r1', 'pdf', 'a', b'%PDF-')
        os.remove(os.path.join(queue.directory, f'{job.id}.bin'))
        u, events = uploader(queue, FakeApi())
        assert u.run_once() == 0 and queue.pending_count() == 0
        assert events[0][0] == 'rejected'

    def test_a_failing_listener_never_breaks_sending(self, queue:UploadQueue) -> None:
        queue.add('r1', 'pdf', 'a', b'%PDF-')
        api = FakeApi()
        u = Uploader(queue, api=api)

        def bad(_e:str, _j:Job|None, _r:str) -> None:
            raise RuntimeError('boom')

        u.add_listener(bad)
        assert u.run_once() == 1 and queue.pending_count() == 0

    def test_a_newer_file_queued_meanwhile_is_what_gets_sent(self, queue:UploadQueue) -> None:
        queue.add('r1', 'alog', 'a.alog', b"{'v': 1}")
        queue.add('r1', 'alog', 'a.alog', b"{'v': 2}")
        api = FakeApi()
        uploader(queue, api)[0].run_once()
        assert [c['content'] for c in api.calls] == [b"{'v': 2}"]


class TestThread:
    def test_the_thread_sends_a_queued_file_and_stops_cleanly(self, queue:UploadQueue) -> None:
        api = FakeApi()
        u, events = uploader(queue, api)
        u.start()
        try:
            queue.add('r1', 'pdf', 'a', b'%PDF-')
            u.wake()
            deadline = time.monotonic() + 3
            while queue.pending_count() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert queue.pending_count() == 0 and len(api.calls) == 1
        finally:
            u.stop()
        assert not any(t.name == 'erp-upload' and t.is_alive() for t in threading.enumerate())
        assert events[0][0] == 'changed'   # start() reports the size of the queue it found

    def test_start_does_not_wait_for_a_delay_of_the_previous_run(self, queue:UploadQueue, clock:Clock) -> None:
        job = queue.add('r1', 'pdf', 'a', b'%PDF-')
        queue.retry_later(job, 'network')
        assert queue.due() == []
        api = FakeApi()
        u, _ = uploader(queue, api)
        u.start()
        try:
            deadline = time.monotonic() + 3
            while queue.pending_count() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert queue.pending_count() == 0 and clock.now > 0
        finally:
            u.stop()

    def test_stop_leaves_the_queue_on_disk(self, queue:UploadQueue) -> None:
        queue.add('r1', 'pdf', 'a', b'%PDF-')
        api = FakeApi()
        api.errors = [roasters.RoasterUnavailable('network', 'x')] * 50
        u, _ = uploader(queue, api)
        u.start()
        time.sleep(0.2)
        u.stop()
        assert queue.pending_count() == 1
