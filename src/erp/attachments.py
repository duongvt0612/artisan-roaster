#!/usr/bin/python
#
# attachments.py
#
# ABOUT
# The files of a finished roast batch (the .alog profile and the PDF report) have to reach the ERP, and must never get lost
# because the network or the ERP happens to be down at that moment. So they are first put in a durable queue on this PC, then a
# background thread sends them one by one and retries with a growing delay until the ERP has taken them.
#   * temporary trouble (network, ERP busy/failing, batch not completed yet): try again later, the file stays queued;
#   * the ERP refuses this very file (wrong content, not our batch, batch unknown): never again, the file is kept in `rejected/`
#     together with the reason and the user is told once;
#   * the ERP login expired: pause until the user logged in again.
# No Qt here: dependencies (API, clock, directory) are injected so that everything is testable without a network or a GUI.

# LICENSE
# This program or module is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published
# by the Free Software Foundation, either version 2 of the License, or
# version 3 of the License, or (at your option) any later version. It is
# provided for educational purposes and is distributed in the hope that
# it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
# the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Final

from erp import config, roasters, rounds

_log: Final[logging.Logger] = logging.getLogger(__name__)

QUEUE_DIR_NAME: Final[str] = 'erp_upload_queue'
REJECTED_DIR_NAME: Final[str] = 'rejected'
BACKOFF_FIRST_SECONDS: Final[float] = 30.0
BACKOFF_MAX_SECONDS: Final[float] = 600.0
POLL_SECONDS: Final[float] = 5.0
KINDS: Final[tuple[str, ...]] = ('alog', 'pdf')


def backoff_seconds(attempts:int) -> float:
    """30 s after the first failure, then doubling (60, 120, 240, 480), at most 10 minutes."""
    return float(min(BACKOFF_MAX_SECONDS, BACKOFF_FIRST_SECONDS * (2 ** max(0, attempts - 1))))


def default_directory() -> str:
    """<Artisan's local data directory>/erp_upload_queue/<environment>: next to the settings, survives restarts and crashes, and a file
    waiting for one ERP is never sent to another one (the batch ids only mean something to the ERP they come from)."""
    base:str|None = None
    try:
        from artisanlib.util import getDataDirectory # pylint: disable=import-outside-toplevel
        base = getDataDirectory()
    except Exception as e: # pylint: disable=broad-except
        _log.info('no data directory: %s', type(e).__name__)
    if not base:
        base = os.path.join(os.path.expanduser('~'), '.lvg-artisan')
    try:
        env = config.environment()
    except config.ConfigError:
        env = 'unconfigured'
    return os.path.join(base, QUEUE_DIR_NAME, env)


@dataclass
class Job:
    id:str
    round_id:str
    kind:str
    filename:str
    sha256:str
    size_bytes:int
    created_at:float
    attempts:int = 0
    next_try_at:float = 0.0
    last_error:str = ''


class UploadQueue:
    """Files waiting for the ERP: one `<id>.bin` (the content) and one `<id>.json` (what it is, how often it was tried) per file.

    A job exists once its .json is there; both files are written to a temporary name first and renamed, so a crash never leaves a
    half written job. There is at most one job per (batch, kind): a newer file replaces the older one."""

    def __init__(self, directory:str|None = None, clock:Callable[[], float] = time.time) -> None:
        self.directory:str = directory if directory is not None else default_directory()
        self._clock:Callable[[], float] = clock
        self._lock:threading.RLock = threading.RLock()

    # ---- paths ----------------------------------------------------------------------------------------------------------

    def _path(self, job_id:str, ext:str) -> str:
        return os.path.join(self.directory, f'{job_id}.{ext}')

    @property
    def rejected_directory(self) -> str:
        return os.path.join(self.directory, REJECTED_DIR_NAME)

    @staticmethod
    def _write_atomic(path:str, content:bytes) -> None:
        tmp = f'{path}.{uuid.uuid4().hex[:8]}.tmp'
        with open(tmp, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _save_meta(self, job:Job) -> None:
        self._write_atomic(self._path(job.id, 'json'), json.dumps(asdict(job), ensure_ascii=False).encode('utf-8'))

    # ---- queue ----------------------------------------------------------------------------------------------------------

    def add(self, round_id:str, kind:str, filename:str, content:bytes) -> Job:
        """Queues a file. A file of the same batch and kind that was still waiting is replaced (the newer one is the one that counts)."""
        if kind not in KINDS:
            raise ValueError(f'unknown kind {kind!r}')
        with self._lock:
            os.makedirs(self.directory, exist_ok=True)
            for old in self.pending():
                if old.round_id == round_id and old.kind == kind:
                    self.done(old)
            job = Job(id=uuid.uuid4().hex, round_id=round_id, kind=kind, filename=filename, sha256=hashlib.sha256(content).hexdigest(),
                      size_bytes=len(content), created_at=self._clock())
            self._write_atomic(self._path(job.id, 'bin'), content)
            self._save_meta(job) # the job exists from here on
            return job

    def pending(self) -> list[Job]:
        """All waiting jobs, oldest first. A job whose description cannot be read is set aside, never retried blindly."""
        jobs:list[Job] = []
        with self._lock:
            try:
                names = os.listdir(self.directory)
            except FileNotFoundError:
                return []
            for name in names:
                if not name.endswith('.json'):
                    continue
                job_id = name[:-5]
                try:
                    with open(self._path(job_id, 'json'), encoding='utf-8') as f:
                        jobs.append(Job(**json.load(f)))
                except Exception as e: # pylint: disable=broad-except
                    _log.info('unreadable queue entry %s: %s', job_id, type(e).__name__)
                    self._set_aside(job_id, 'the description of the job is not readable')
        jobs.sort(key=lambda j: (j.created_at, j.id))
        return jobs

    def pending_count(self) -> int:
        return len(self.pending())

    def due(self) -> list[Job]:
        now = self._clock()
        return [j for j in self.pending() if j.next_try_at <= now]

    def read(self, job:Job) -> bytes:
        with open(self._path(job.id, 'bin'), 'rb') as f:
            return f.read()

    def done(self, job:Job) -> None:
        with self._lock:
            for ext in ('json', 'bin'): # the description first: without it the job does not exist any more
                try:
                    os.remove(self._path(job.id, ext))
                except FileNotFoundError:
                    pass

    def retry_later(self, job:Job, error:str) -> Job:
        with self._lock:
            job.attempts += 1
            job.last_error = error[:300]
            job.next_try_at = self._clock() + backoff_seconds(job.attempts)
            self._save_meta(job)
            return job

    def retry_now(self) -> None:
        """Makes every waiting job due now (after the user logged in again, or a new Artisan start)."""
        with self._lock:
            for job in self.pending():
                if job.next_try_at > 0:
                    job.next_try_at = 0.0
                    self._save_meta(job)

    def reject(self, job:Job, reason:str) -> None:
        """Gives a job up for good: the file moves to rejected/ with the reason, so that nothing is lost silently."""
        self._set_aside(job.id, reason, job)

    def _set_aside(self, job_id:str, reason:str, job:Job|None = None) -> None:
        with self._lock:
            try:
                os.makedirs(self.rejected_directory, exist_ok=True)
                for ext in ('bin', 'json'):
                    src = self._path(job_id, ext)
                    if os.path.exists(src):
                        os.replace(src, os.path.join(self.rejected_directory, f'{job_id}.{ext}'))
                note = {'reason': reason, **(asdict(job) if job is not None else {})}
                self._write_atomic(os.path.join(self.rejected_directory, f'{job_id}.reason.json'), json.dumps(note, ensure_ascii=False).encode('utf-8'))
            except OSError as e:
                _log.info('could not set the job %s aside: %s', job_id, type(e).__name__)

    def cleanup_orphans(self) -> None:
        """Removes what a crash left behind: temporary files and contents whose description was never written."""
        with self._lock:
            try:
                names = set(os.listdir(self.directory))
            except FileNotFoundError:
                return
            for name in names:
                orphan = name.endswith('.tmp') or (name.endswith('.bin') and f'{name[:-4]}.json' not in names)
                if orphan:
                    try:
                        os.remove(os.path.join(self.directory, name))
                    except OSError:
                        pass


# events of the uploader
SENT:Final[str] = 'sent'
REJECTED:Final[str] = 'rejected'
CHANGED:Final[str] = 'changed'

Listener = Callable[[str, Job|None, str], None]  # (event, job, reason)


class Uploader:
    """Sends the queued files in the background. run_once() does one pass and is what the tests drive; start() runs it on a thread."""

    def __init__(
        self,
        queue:UploadQueue,
        *,
        api:Any = rounds,
        interval:float = POLL_SECONDS,
    ) -> None:
        self.queue:UploadQueue = queue
        self._api:Any = api
        self._interval:float = interval
        self._listeners:list[Listener] = []
        self._wake:threading.Event = threading.Event()
        self._stop:threading.Event = threading.Event()
        self._thread:threading.Thread|None = None
        self._lock:threading.Lock = threading.Lock() # one pass at a time
        self.paused:bool = False # the ERP login expired: nothing is tried until resume()

    def add_listener(self, listener:Listener) -> None:
        """listener(event, job, reason) is called from the uploader thread: 'sent', 'rejected' (reason says why) or 'changed' (queue size)."""
        self._listeners.append(listener)

    def _emit(self, event:str, job:Job|None = None, reason:str = '') -> None:
        for listener in list(self._listeners):
            try:
                listener(event, job, reason)
            except Exception as e: # pylint: disable=broad-except
                _log.exception(e)

    def run_once(self) -> int:
        """One pass over the jobs that are due. Returns how many files the ERP took."""
        if self.paused:
            return 0
        sent = 0
        with self._lock:
            for job in self.queue.due():
                if self._stop.is_set() or self.paused:
                    break
                outcome = self._send(job)
                if outcome == 'sent':
                    sent += 1
                if outcome == 'paused':
                    break
        return sent

    def _send(self, job:Job) -> str:
        try:
            content = self.queue.read(job)
        except OSError:
            self.queue.reject(job, 'the file is missing on this PC')
            self._emit(REJECTED, job, 'the file is missing on this PC')
            self._emit(CHANGED)
            return 'rejected'
        try:
            self._api.upload_file(job.round_id, kind=job.kind, filename=job.filename, content=content, sha256=job.sha256)
        except roasters.SessionExpired:
            self.paused = True # not the file's fault: wait for the user to log in again
            return 'paused'
        except (rounds.FileRejected, roasters.RoasterForbidden, roasters.RoasterNotFound) as e:
            self.queue.reject(job, e.message)
            self._emit(REJECTED, job, e.message)
            self._emit(CHANGED)
            return 'rejected'
        except (roasters.RoasterUnavailable, rounds.RoundNotCompleted) as e:
            self.queue.retry_later(job, e.message)
            return 'later'
        except roasters.RoasterError as e:
            # any other refusal (e.g. a 400 about the request itself) cannot be cured by waiting
            self.queue.reject(job, e.message)
            self._emit(REJECTED, job, e.message)
            self._emit(CHANGED)
            return 'rejected'
        except Exception as e: # pylint: disable=broad-except
            _log.exception(e)
            self.queue.retry_later(job, type(e).__name__)
            return 'later'
        self.queue.done(job)
        self._emit(SENT, job)
        self._emit(CHANGED)
        return 'sent'

    # ---- thread ---------------------------------------------------------------------------------------------------------

    def resume(self) -> None:
        """The user logged in again: try everything that is waiting right away."""
        self.paused = False
        self.queue.retry_now()
        self._wake.set()

    def wake(self) -> None:
        """A file was just queued: send it now instead of at the next poll."""
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self.queue.cleanup_orphans()
        self.queue.retry_now() # a new run of Artisan does not wait for a delay set by the previous one
        self._thread = threading.Thread(target=self._run, name='erp-upload', daemon=True)
        self._thread.start()
        self._emit(CHANGED)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e: # pylint: disable=broad-except
                _log.exception(e)
            self._wake.wait(self._interval)
            self._wake.clear()

    def stop(self, wait:bool = True) -> None:
        """Stops the thread (waiting a few seconds for the file being sent, unless wait is False). Files still queued stay on disk."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None
