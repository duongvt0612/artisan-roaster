#!/usr/bin/python
#
# batch.py
#
# ABOUT
# The roast batch ("mẻ rang") this Artisan has started on the ERP and not finished yet.
# The ERP is the source of truth (after a crash Artisan asks it which batch runs on the roaster, see rounds.active_round), so nothing
# is stored here. Pure data, no Qt widgets: the dialogs and the flow in roast_flow.py build on it.

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

from dataclasses import dataclass
from typing import Any

from erp.rounds import ActiveRound, RoastCommand, StartedRound


@dataclass(frozen=True)
class Batch:
    """A batch that is running on the ERP. All weights are in kg."""
    round_id:str
    round_number:int
    command_id:str
    command_code:str
    kind:str
    output_name:str
    output_unit:str
    input_kg:float
    expected_output_kg:float
    tolerance_percent:float
    started_at:str          # ISO 8601, as the ERP sent it ('' if it did not)
    equipment_id:str

    @property
    def label(self) -> str:
        """'LS-1026-000123 · mẻ #3', what the operator sees in the title bar and the banner."""
        return f'{self.command_code} · mẻ #{self.round_number}'

    @property
    def is_test(self) -> bool:
        return self.kind == 'test'

    def profile_link(self) -> dict[str, Any]:
        """What Artisan writes into the profile (.alog) so that the profile can be tied to exactly this batch."""
        return {'erpRoundId': self.round_id, 'erpCommandCode': self.command_code, 'erpRoundNumber': self.round_number}


def from_started(started:StartedRound, command:RoastCommand, input_kg:float, equipment_id:str) -> Batch:
    return Batch(
        round_id=started.round_id, round_number=started.round_number, command_id=started.command_id, command_code=command.code,
        kind=command.kind, output_name=command.output_name, output_unit=command.output_unit, input_kg=round(input_kg, 4),
        expected_output_kg=started.expected_output_kg, tolerance_percent=started.tolerance_percent,
        started_at=started.started_at.isoformat() if started.started_at is not None else '', equipment_id=equipment_id)


def from_active(active:ActiveRound, equipment_id:str) -> Batch:
    return Batch(
        round_id=active.round_id, round_number=active.round_number, command_id=active.command_id, command_code=active.command_code,
        kind=active.kind, output_name=active.output_name, output_unit=active.output_unit, input_kg=active.input_kg,
        expected_output_kg=active.expected_output_kg, tolerance_percent=active.tolerance_percent,
        started_at=active.started_at.isoformat() if active.started_at is not None else '', equipment_id=equipment_id)


def loss_percent(input_kg:float, output_kg:float) -> float:
    """(in − out) / in × 100, like the ERP computes it. 0 if nothing went in."""
    if input_kg <= 0:
        return 0.0
    return (input_kg - output_kg) / input_kg * 100
