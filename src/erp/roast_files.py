#!/usr/bin/python
#
# roast_files.py
#
# ABOUT
# Builds the files of a finished roast batch from what Artisan shows right now, and puts them in the upload queue:
#   * the .alog: the profile exactly as Artisan would save it (it carries erpRoundId, which is how the ERP knows it belongs to the batch);
#   * the PDF: Artisan's own roast report.
# Nothing here talks to the ERP: sending (and retrying) is the business of attachments.Uploader.
# A failing PDF never keeps the .alog from being sent, and neither of them ever blocks the operator.

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

import importlib.util
import logging
import os
import re
import tempfile
from collections.abc import Callable
from typing import Final, TYPE_CHECKING

from erp.attachments import Job, UploadQueue
from erp.batch import Batch

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)

PDF_MAGIC: Final[bytes] = b'%PDF-'


def file_stem(batch:Batch) -> str:
    """'LS-1026-000123-m3': the name the ERP shows for the files of the batch (letters, digits, dot, dash, underscore)."""
    name = re.sub(r'[^\w.\-]+', '_', f'{batch.command_code}-m{batch.round_number}', flags=re.ASCII)
    return re.sub(r'\.{2,}', '_', name).strip('._-') or 'roast'


def build_alog(aw:'ApplicationWindow', batch:Batch) -> bytes|None:
    """The profile as Artisan saves it (a Python literal, `repr` of the profile dict), or None if it is not the recording of this batch."""
    try:
        profile = aw.getProfile(generate_hash=True)
    except Exception as e: # pylint: disable=broad-except
        _log.exception(e)
        return None
    if not profile or profile.get('erpRoundId') != batch.round_id:
        _log.info('the profile shown is not the recording of batch %s: no .alog is sent', batch.round_id)
        return None
    return repr(profile).encode('utf-8')


def build_pdf(aw:'ApplicationWindow', *, busy:Callable[[bool], None]|None = None) -> bytes|None:
    """Artisan's roast report as PDF, or None if it could not be made (no PDF engine, report failed)."""
    if importlib.util.find_spec('pyfulgur') is None:
        _log.info('pyfulgur is not installed: no PDF report is sent')
        return None
    fd, path = tempfile.mkstemp(suffix='.pdf', prefix='erp-roast-')
    os.close(fd)
    try:
        if busy is not None:
            busy(True)
        try:
            aw.roastReport(pdf_filename=path) # draws the graph: has to run on the GUI thread, takes a few seconds
        finally:
            if busy is not None:
                busy(False)
        with open(path, 'rb') as f:
            content = f.read()
    except Exception as e: # pylint: disable=broad-except
        _log.exception(e)
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not content.startswith(PDF_MAGIC):
        _log.info('the report is not a PDF: not sent')
        return None
    return content


def build_and_enqueue(
    aw:'ApplicationWindow',
    batch:Batch,
    queue:UploadQueue,
    *,
    busy:Callable[[bool], None]|None = None,
    pdf:bool = True,
) -> list[Job]:
    """Queues the .alog and the PDF of the batch. Returns the jobs that were queued (an empty list is not an error: see the log)."""
    jobs:list[Job] = []
    stem = file_stem(batch)
    alog = build_alog(aw, batch)
    if alog is not None:
        jobs.append(queue.add(batch.round_id, 'alog', f'{stem}.alog', alog))
    if alog is not None and pdf: # the report of a recording that is not this batch's would be the wrong report
        report = build_pdf(aw, busy=busy)
        if report is not None:
            jobs.append(queue.add(batch.round_id, 'pdf', f'{stem}.pdf', report))
    return jobs


def wait_cursor() -> Callable[[bool], None]:
    """A `busy` callback that shows the wait cursor while the report is made."""
    from PyQt6.QtCore import Qt # pylint: disable=import-outside-toplevel
    from PyQt6.QtWidgets import QApplication # pylint: disable=import-outside-toplevel

    def busy(on:bool) -> None:
        if on:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
        else:
            QApplication.restoreOverrideCursor()

    return busy
