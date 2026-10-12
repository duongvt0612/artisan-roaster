#!/usr/bin/python
#
# gate.py
#
# ABOUT
# The mandatory ERP login: the main window is only shown if require_login() returned True.
# There is deliberately no offline mode, if the ERP cannot be reached the user cannot enter.

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
import threading
from typing import Any, Final, TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from erp import banner as erp_banner, config, device, realtime as erp_realtime
from erp.client import ErpAuthError
from erp.lease import LeaseManager, LeaseState
from erp.login import erp_login, error_text
from erp.roaster_dialog import RoasterDialog
from erp.roasters import Claim, Roaster
from erp.session import session

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow # noqa: F401 # pylint: disable=unused-import

_log: Final[logging.Logger] = logging.getLogger(__name__)


def ask_retry(error:ErpAuthError) -> bool:
    """Tells the user the ERP could not be reached. Returns True if he wants to try again."""
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle('ERP')
    box.setText(QApplication.translate('ERP', 'Không thể xác thực với ERP'))
    box.setInformativeText(error_text(error))
    retry = box.addButton(QApplication.translate('ERP', 'Thử lại'), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QApplication.translate('ERP', 'Thoát'), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(retry)
    box.exec()
    return box.clickedButton() is retry


def confirm_logout(aw:'ApplicationWindow') -> bool:
    box = QMessageBox(aw)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle('ERP')
    box.setText(QApplication.translate('ERP', 'Đăng xuất khỏi ERP và thoát Artisan?'))
    box.setInformativeText(QApplication.translate(
        'ERP', 'Tài khoản này cũng sẽ bị đăng xuất khỏi các thiết bị khác đang dùng ERP.'))
    yes = box.addButton(QApplication.translate('ERP', 'Đăng xuất'), QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QApplication.translate('ERP', 'Huỷ'), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is yes


def require_login(aw:'ApplicationWindow') -> bool:
    """Returns True only if the user is logged in to the ERP."""
    while True:
        try:
            if session.restore():
                return True
            break
        except ErpAuthError as e:
            # network down, ERP failing or rate limited: the stored session is kept, but the user stays out until it is verified
            _log.info('ERP session could not be verified: %s', e.kind)
            if not ask_retry(e):
                return False
    return erp_login(aw, session.stored_username())


def logout(aw:'ApplicationWindow') -> bool:
    """Ends the ERP session after confirmation. Returns True if logged out; the caller is expected to quit the application."""
    if not confirm_logout(aw):
        return False
    release_roaster() # give the roaster back first: the ERP logout would release it too, but then our heartbeat thread is still running
    session.logout()
    return True


# ---- the roaster this session holds ------------------------------------------------------------------------------------------

PERM_FORCE_RELEASE: Final[str] = 'roasters:force-release'
RELEASE_WAIT_SECONDS: Final[float] = 3.0 # how long quitting Artisan waits for the ERP to confirm the release


class _Bridge(QObject):
    """Moves lease state changes from the heartbeat thread to the GUI thread."""
    state_changed = pyqtSignal(object, str)


class _State:
    """What the running Artisan knows about its roaster. One per process, see _state."""

    def __init__(self) -> None:
        self.aw:Any = None
        self.manager:LeaseManager|None = None
        self.realtime:erp_realtime.RealtimeClient|None = None
        self.dialog:RoasterDialog|None = None
        self.banner:erp_banner.LeaseBanner|None = None
        self.bridge:_Bridge|None = None


_state:_State = _State()


def _reset_state() -> None:
    """For tests."""
    global _state # pylint: disable=global-statement
    _state = _State()


def current_lease() -> LeaseManager|None:
    """The lease manager of the roaster this session holds (None before one is chosen or after quitting)."""
    return _state.manager


def has_roaster() -> bool:
    m = _state.manager
    return m is not None and m.is_ok


def permission_codes(user:dict[str, Any]|None) -> set[str]:
    """The permission codes of an ERP user (as returned by the login: roles → permissions → code)."""
    codes:set[str] = set()
    for role in (user or {}).get('roles') or []:
        for perm in (role or {}).get('permissions') or []:
            code = (perm or {}).get('code')
            if isinstance(code, str):
                codes.add(code)
    return codes


def _user_name(user:dict[str, Any]|None) -> str:
    user = user or {}
    return str(user.get('fullName') or user.get('username') or '')


def _on_realtime_changed(_payload:dict[str, Any]) -> None:
    dialog = _state.dialog
    if dialog is not None:
        dialog.request_refresh()


def _on_realtime_lease_lost(payload:dict[str, Any]) -> None:
    manager = _state.manager
    if manager is not None:
        manager.notify_lease_lost(str(payload.get('equipmentId') or ''), str(payload.get('reason') or 'lease_lost'))


def _start_realtime() -> None:
    """The realtime channel only speeds things up; Artisan is correct without it."""
    if _state.realtime is not None or not erp_realtime.is_available():
        return
    client = erp_realtime.RealtimeClient(
        url=config.get_socket_url(),
        get_token=session.fresh_access_token,
        on_changed=_on_realtime_changed,
        on_lease_lost=_on_realtime_lease_lost,
    )
    client.start()
    _state.realtime = client


def _stop_realtime() -> None:
    client = _state.realtime
    _state.realtime = None
    if client is not None:
        client.stop()


def _install_banner(aw:'ApplicationWindow') -> None:
    """The banner is cosmetic: failing to show it must never keep the user out."""
    if _state.banner is not None:
        return
    try:
        bar = erp_banner.install(aw.main_widget)
        bridge = _Bridge()
        bridge.state_changed.connect(_on_lease_state)
        bar.reselect.connect(reselect_roaster)
        _state.banner = bar
        _state.bridge = bridge
    except Exception as e: # pylint: disable=broad-except
        _log.exception(e)


def _on_lease_state(state:LeaseState, reason:str) -> None:
    manager = _state.manager
    if _state.banner is not None and manager is not None:
        _state.banner.apply(state, manager.roaster_name, reason)


def _adopt(roaster:Roaster, claim:Claim) -> None:
    """Starts keeping the freshly claimed roaster alive (and gives back a previous one, if any)."""
    previous = _state.manager
    if previous is not None:
        previous.stop(release=previous.is_ok)
    manager = LeaseManager(roaster.id, roaster.name or roaster.code, claim, session_id=device.session_id())
    bridge = _state.bridge
    if bridge is not None:
        manager.add_listener(bridge.state_changed.emit)
    _state.manager = manager
    manager.start()
    if _state.banner is not None:
        _state.banner.apply(LeaseState.HELD, manager.roaster_name, '')


def _pick(aw:'ApplicationWindow') -> str:
    """Shows the roaster selection. Returns 'ok' (a roaster is held now), 'login' (the ERP login expired) or 'cancel'."""
    user = session.user
    dialog = RoasterDialog(None, aw, user_name=_user_name(user), can_force_release=PERM_FORCE_RELEASE in permission_codes(user))
    _state.dialog = dialog
    try:
        accepted = bool(dialog.exec())
        claim, roaster, need_login = dialog.claim, dialog.roaster, dialog.need_login
    finally:
        _state.dialog = None
        dialog.deleteLater()
    if accepted and claim is not None and roaster is not None:
        _adopt(roaster, claim)
        return 'ok'
    return 'login' if need_login else 'cancel'


def require_roaster(aw:'ApplicationWindow') -> bool:
    """Right after the ERP login: the user MUST choose a roaster (and holds it exclusively). False means: do not let him in."""
    _state.aw = aw
    _install_banner(aw)
    _start_realtime()
    while True:
        outcome = _pick(aw)
        if outcome == 'ok':
            return True
        if outcome == 'login' and require_login(aw):
            continue # the login had expired meanwhile; he logged in again, choose now
        _stop_realtime()
        return False


def reselect_roaster() -> bool:
    """After the roaster was lost: choose again from the running main window. Cancelling just leaves the warning banner up."""
    aw = _state.aw
    if aw is None:
        return False
    outcome = _pick(aw)
    if outcome == 'login' and require_login(aw):
        outcome = _pick(aw)
    return outcome == 'ok'


def release_roaster(timeout:float = RELEASE_WAIT_SECONDS) -> None:
    """Gives the roaster back (if still ours) and stops the background work. Safe to call twice; never hangs Artisan's quit for long."""
    manager = _state.manager
    _state.manager = None
    _stop_realtime()
    if manager is None:
        return
    worker = threading.Thread(target=manager.stop, name='erp-lease-release', daemon=True)
    worker.start()
    worker.join(timeout)
