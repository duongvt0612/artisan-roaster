#!/usr/bin/python
#
# rounds.py
#
# ABOUT
# Client of the ERP roasting API made for Artisan (/api/artisan): find the production commands that can be roasted, start a
# roast batch ("mẻ rang") on the roaster this session holds, finish it with the weight of the roasted beans, or cancel it.
# The ERP answers synchronously and checks the roaster lease in the same transaction as the write.
# Only RoasterError subclasses leave this module.

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

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from erp import config
from erp.roasters import RoasterError, RoasterUnavailable, request

_log: Final[logging.Logger] = logging.getLogger(__name__)


class RoasterBusy(RoasterError):
    """The roaster already has a batch that was not finished (ROASTER_BUSY)."""


class QuotaExceeded(RoasterError):
    """More beans declared than what is left, or beans that do not belong to the command (QUOTA_EXCEEDED)."""


class NotRoastable(RoasterError):
    """The command or its roasting step is finished, cancelled or not a roasting one (COMMAND_NOT_ROASTABLE)."""


class RoundNotRunning(RoasterError):
    """The batch was finished or cancelled already (ROUND_NOT_RUNNING)."""


class RoundNotCompleted(RoasterError):
    """The ERP does not take files of a batch that is not completed (yet): ROUND_NOT_COMPLETED. Worth a retry."""


class FileRejected(RoasterError):
    """The ERP refuses this very file (FILE_INVALID, FILE_ROUND_MISMATCH): sending it again will not help."""


_CONFLICTS:Final[dict[str, type[RoasterError]]] = {
    'ROASTER_BUSY': RoasterBusy,
    'QUOTA_EXCEEDED': QuotaExceeded,
    'COMMAND_NOT_ROASTABLE': NotRoastable,
    'ROUND_NOT_RUNNING': RoundNotRunning,
    'ROUND_NOT_COMPLETED': RoundNotCompleted,
    'FILE_INVALID': FileRejected,
    'FILE_ROUND_MISMATCH': FileRejected,
}


@dataclass(frozen=True)
class RoundInput:
    """A kind of green coffee. `kg` is what is still available (search) or what the batch uses (active batch)."""
    inventory_id:str
    code:str
    name:str
    unit:str
    kg:float


@dataclass(frozen=True)
class SaleOrder:
    order_number:str
    customer_name:str


@dataclass(frozen=True)
class RoastCommand:
    command_id:str
    code:str
    kind:str                 # normal | test
    status:str               # pending | in_progress
    step_number:int
    output_name:str
    output_code:str
    output_unit:str
    target_kg:float
    produced_kg:float
    remaining_kg:float
    request_code:str
    sale_orders:tuple[SaleOrder, ...]
    inputs:tuple[RoundInput, ...]

    @property
    def is_test(self) -> bool:
        return self.kind == 'test'

    @property
    def in_progress(self) -> bool:
        return self.status == 'in_progress'


@dataclass(frozen=True)
class StartedRound:
    round_id:str
    round_number:int
    command_id:str
    step_number:int
    expected_output_kg:float
    tolerance_percent:float
    started_at:datetime|None


@dataclass(frozen=True)
class FinishedRound:
    round_id:str
    round_number:int
    output_kg:float
    expected_output_kg:float
    loss_percent:float
    loss_exceeded:bool
    tolerance_percent:float


@dataclass(frozen=True)
class ActiveRound:
    """A batch that is running on a roaster, as the ERP knows it."""
    round_id:str
    round_number:int
    command_id:str
    command_code:str
    kind:str
    step_number:int
    output_name:str
    output_unit:str
    target_kg:float
    expected_output_kg:float
    tolerance_percent:float
    started_at:datetime|None
    started_by_id:str
    started_by_name:str
    from_artisan:bool
    mine:bool
    inputs:tuple[RoundInput, ...]

    @property
    def input_kg(self) -> float:
        return round(sum(i.kg for i in self.inputs), 3)


def _num(value:Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text(value:Any) -> str:
    return value if isinstance(value, str) else ''


def _dt(value:Any) -> datetime|None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _input(item:Any, key:str) -> RoundInput:
    return RoundInput(_text(item['inventoryId']), _text(item.get('code')), _text(item.get('name')), _text(item.get('unit')), _num(item.get(key)))


def _command(item:Any) -> RoastCommand|None:
    try:
        output = item.get('output') or {}
        source = item.get('source') or {}
        return RoastCommand(
            command_id=_text(item['commandId']),
            code=_text(item['code']),
            kind=_text(item.get('kind')) or 'normal',
            status=_text(item.get('status')),
            step_number=int(item['stepNumber']),
            output_name=_text(output.get('name')),
            output_code=_text(output.get('code')),
            output_unit=_text(output.get('unit')) or 'kg',
            target_kg=_num(item.get('targetQuantity')),
            produced_kg=_num(item.get('producedQuantity')),
            remaining_kg=_num(item.get('remainingQuantity')),
            request_code=_text(source.get('requestCode')),
            sale_orders=tuple(SaleOrder(_text(o.get('orderNumber')), _text(o.get('customerName'))) for o in (source.get('saleOrders') or [])),
            inputs=tuple(_input(i, 'availableKg') for i in (item.get('inputs') or [])),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        _log.info('skipping a malformed roasting command')
        return None


def _bad_reply() -> RoasterError:
    return RoasterUnavailable('server', 'Phản hồi của ERP không đúng định dạng')


def search_commands(query:str, limit:int = 20) -> list[RoastCommand]:
    """The roasting commands that can still be roasted whose code, output, request, sale order or customer match `query`."""
    data = request('GET', f'{config.get_artisan_url()}/roasting-commands', params={'q': query.strip(), 'limit': limit},
                   conflicts=_CONFLICTS)
    if not isinstance(data, list):
        raise _bad_reply()
    return [c for c in (_command(item) for item in data) if c is not None]


def start_round(*, equipment_id:str, session_id:str, version:int, command_id:str, step_number:int,
                inputs:list[tuple[str, float]], notes:str|None = None) -> StartedRound:
    """Starts a batch of `command_id` on the roaster this session holds. `inputs` are (inventory_id, kg)."""
    body:dict[str, Any] = {
        'equipmentId': equipment_id, 'sessionId': session_id, 'version': version, 'commandId': command_id, 'stepNumber': step_number,
        'inputs': [{'inventoryId': inventory_id, 'quantity': kg} for inventory_id, kg in inputs],
    }
    if notes:
        body['notes'] = notes
    data = request('POST', f'{config.get_artisan_url()}/rounds/start', json=body, conflicts=_CONFLICTS)
    try:
        return StartedRound(
            round_id=_text(data['roundId']), round_number=int(data['roundNumber']), command_id=_text(data.get('commandId')) or command_id,
            step_number=int(data.get('stepNumber') or step_number), expected_output_kg=_num(data.get('expectedOutputQuantity')),
            tolerance_percent=_num(data.get('tolerancePercentage')), started_at=_dt(data.get('startedAt')))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise _bad_reply() from e


def finish_round(round_id:str, *, output_kg:float, notes:str|None = None, powder_color:str|None = None) -> FinishedRound:
    body:dict[str, Any] = {'outputQuantity': output_kg}
    if notes:
        body['notes'] = notes
    if powder_color:
        body['powderColor'] = powder_color
    data = request('POST', f'{config.get_artisan_url()}/rounds/{round_id}/complete', json=body, conflicts=_CONFLICTS)
    try:
        return FinishedRound(
            round_id=_text(data['roundId']), round_number=int(data.get('roundNumber') or 0), output_kg=_num(data.get('outputQuantity')),
            expected_output_kg=_num(data.get('expectedOutputQuantity')), loss_percent=_num(data.get('lossRate')),
            loss_exceeded=bool(data.get('lossExceeded')), tolerance_percent=_num(data.get('tolerancePercentage')))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise _bad_reply() from e


def cancel_round(round_id:str, *, reason:str) -> None:
    request('POST', f'{config.get_artisan_url()}/rounds/{round_id}/cancel', json={'reason': reason}, conflicts=_CONFLICTS)


_CONTENT_TYPES:Final[dict[str, str]] = {'alog': 'application/octet-stream', 'pdf': 'application/pdf'}


@dataclass(frozen=True)
class UploadedFile:
    file_id:str
    kind:str
    size_bytes:int


def upload_file(round_id:str, *, kind:str, filename:str, content:bytes, sha256:str) -> UploadedFile:
    """Sends the .alog (kind 'alog') or the PDF (kind 'pdf') of a finished batch. The ERP checks the content and ties it to the batch.

    Raises RoundNotCompleted (try again later), FileRejected (never again), RoasterForbidden / RoasterNotFound (never again),
    SessionExpired (after logging in again), RoasterUnavailable (try again later)."""
    data = request('POST', f'{config.get_artisan_url()}/rounds/{round_id}/files', conflicts=_CONFLICTS,
                   data={'kind': kind, 'sha256': sha256}, files={'file': (filename, content, _CONTENT_TYPES.get(kind, 'application/octet-stream'))})
    try:
        return UploadedFile(file_id=_text(data['id']), kind=_text(data.get('kind')) or kind, size_bytes=int(data.get('sizeBytes') or len(content)))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise _bad_reply() from e


def active_round(equipment_id:str) -> ActiveRound|None:
    """The batch running on the roaster (started from Artisan or from the ERP), None if the roaster has none."""
    data = request('GET', f'{config.get_artisan_url()}/roasters/{equipment_id}/active-round', conflicts=_CONFLICTS)
    if data is None:
        return None
    try:
        output = data.get('output') or {}
        by = data.get('startedBy') or {}
        return ActiveRound(
            round_id=_text(data['roundId']), round_number=int(data['roundNumber']), command_id=_text(data.get('commandId')),
            command_code=_text(data.get('commandCode')), kind=_text(data.get('kind')) or 'normal', step_number=int(data.get('stepNumber') or 1),
            output_name=_text(output.get('name')), output_unit=_text(output.get('unit')) or 'kg', target_kg=_num(data.get('targetQuantity')),
            expected_output_kg=_num(data.get('expectedOutputQuantity')), tolerance_percent=_num(data.get('tolerancePercentage')),
            started_at=_dt(data.get('startedAt')), started_by_id=_text(by.get('userId')), started_by_name=_text(by.get('fullName')),
            from_artisan=bool(data.get('fromArtisan')), mine=bool(data.get('mine')),
            inputs=tuple(_input(i, 'availableKg') for i in (data.get('inputs') or [])))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise _bad_reply() from e
