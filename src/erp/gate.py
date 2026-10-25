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
from typing import Any, Final, TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from erp import attachments, background, banner as erp_banner, config, device, realtime as erp_realtime, roast_flow
from erp.attachments import Job, UploadQueue, Uploader
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


def show_session_ended(message:str) -> None:
    """The ERP ended the stored session while Artisan was closed (kicked, password changed, ...): says why before asking to log in again."""
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle('ERP')
    box.setText(QApplication.translate('ERP', 'Phiên đăng nhập ERP đã kết thúc'))
    box.setInformativeText(message)
    box.exec()


def confirm_logout(aw:'ApplicationWindow') -> bool:
    box = QMessageBox(aw)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle('ERP')
    box.setText(QApplication.translate('ERP', 'Đăng xuất khỏi ERP và thoát Artisan?'))
    box.setInformativeText(QApplication.translate(
        'ERP', 'Chỉ máy tính này bị đăng xuất; tài khoản của bạn trên web, điện thoại và máy khác không bị ảnh hưởng.'))
    yes = box.addButton(QApplication.translate('ERP', 'Đăng xuất'), QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QApplication.translate('ERP', 'Huỷ'), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is yes


def show_config_error(error:config.ConfigError) -> None:
    """This build has no (valid) ERP server: tells so, in plain words. There is nothing the user can do but install the right build."""
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle('ERP')
    box.setText(QApplication.translate('ERP', 'Không thể kết nối tới ERP'))
    box.setInformativeText(error.message)
    box.exec()


def require_login(aw:'ApplicationWindow') -> bool:
    """Returns True only if the user is logged in to the ERP."""
    try:
        config.check() # which server this build talks to: never guessed
    except config.ConfigError as e:
        _log.info('no usable ERP server in this build: %s', e.message)
        show_config_error(e)
        return False
    while True:
        try:
            # the ERP may take a while (or not answer): the event loop keeps running meanwhile
            if background.call_with_wait(None, QApplication.translate('ERP', 'Đang kiểm tra phiên đăng nhập ERP…'), session.restore):
                _resume_uploads()
                return True
            break
        except ErpAuthError as e:
            # network down, ERP failing or rate limited: the stored session is kept, but the user stays out until it is verified
            _log.info('ERP session could not be verified: %s', e.kind)
            if not ask_retry(e):
                return False
    if session.ended_message:
        show_session_ended(session.ended_message)
    logged_in = erp_login(aw, session.stored_username())
    if logged_in:
        _resume_uploads() # files that waited for the login are sent now
    return logged_in


def logout(aw:'ApplicationWindow') -> bool:
    """Ends the ERP session after confirmation. Returns True if logged out; the caller is expected to quit the application."""
    if not confirm_logout(aw):
        return False
    if not confirm_quit(aw):
        return False
    _state.quit_confirmed = True # the quit that follows must not ask again
    release_roaster() # give the roaster back first: stops our heartbeat thread, then the ERP logout (which also releases this session's roaster) cannot race it
    _end_session(aw)
    return True


def _end_session(aw:'ApplicationWindow') -> None:
    """Tells the ERP (best effort, bounded wait, the event loop keeps running) and forgets the login here whatever the ERP answers."""
    try:
        background.call_with_wait(aw, QApplication.translate('ERP', 'Đang đăng xuất khỏi ERP…'), session.logout, timeout=LOGOUT_WAIT_SECONDS)
    except background.Cancelled:
        _log.info('the ERP did not confirm the logout in time')
    except Exception as e: # pylint: disable=broad-except
        _log.info('ERP logout failed: %s', type(e).__name__)
    session.clear() # idempotent: the stored refresh token must not outlive a logout the ERP did not answer


# ---- the roaster this session holds ------------------------------------------------------------------------------------------

PERM_FORCE_RELEASE: Final[str] = 'roasters:force-release'
RELEASE_WAIT_SECONDS: Final[float] = 3.0 # how long quitting Artisan waits for the ERP to confirm the release
LOGOUT_WAIT_SECONDS: Final[float] = 5.0  # and for the ERP to confirm the logout


class _Bridge(QObject):
    """Moves lease state changes from the heartbeat thread to the GUI thread."""
    state_changed = pyqtSignal(object, str)
    upload_event = pyqtSignal(str, object, str)


class _State:
    """What the running Artisan knows about its roaster. One per process, see _state."""

    def __init__(self) -> None:
        self.aw:Any = None
        self.manager:LeaseManager|None = None
        self.realtime:erp_realtime.RealtimeClient|None = None
        self.dialog:RoasterDialog|None = None
        self.banner:erp_banner.LeaseBanner|None = None
        self.bridge:_Bridge|None = None
        self.capacity_kg:float|None = None  # what the chosen roaster can take in one batch, None if the ERP does not know
        self.queue:UploadQueue|None = None   # the files of finished batches that are waiting for the ERP
        self.uploader:Uploader|None = None   # and the thread that sends them
        self.quit_confirmed:bool = False    # the operator already agreed to quit with a batch still open (logging out asks first)


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


def _on_realtime_session_revoked(reason:str) -> None:
    """The ERP ended our login session (kicked, password changed, ...): forget it. The roaster is not ours any more either, say so at once."""
    session.revoke(reason)
    manager = _state.manager
    if manager is not None:
        manager.notify_session_revoked(reason)


def _start_realtime() -> None:
    """The realtime channel only speeds things up; Artisan is correct without it."""
    if _state.realtime is not None or not erp_realtime.is_available():
        return
    client = erp_realtime.RealtimeClient(
        url=config.get_socket_url(),
        get_token=session.fresh_access_token,
        on_changed=_on_realtime_changed,
        on_lease_lost=_on_realtime_lease_lost,
        on_session_revoked=_on_realtime_session_revoked,
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
        _ensure_bridge()
        bar.reselect.connect(reselect_roaster)
        bar.finish_batch.connect(lambda: roast_flow.finish_batch(aw))
        _state.banner = bar
        roast_flow.set_listener(_on_batch_changed)
    except Exception as e: # pylint: disable=broad-except
        _log.exception(e)


def window_title_suffix() -> str:
    """Text appended to Artisan's window title: which roaster this session holds. Always visible, even if the banner is overlooked."""
    label = config.environment_label()
    tag = f' [{label}]' if label else '' # a test environment is always visible in the title
    manager = _state.manager
    if manager is None:
        return tag
    name = manager.roaster_name
    if manager.state is LeaseState.LOST:
        text = QApplication.translate('ERP', ' · KHÔNG CÒN GIỮ MÁY {0}').format(name)
    elif manager.state is LeaseState.SUSPECT:
        text = QApplication.translate('ERP', ' · Máy rang: {0} (mất kết nối ERP)').format(name)
    else:
        text = QApplication.translate('ERP', ' · Máy rang: {0}').format(name)
    return tag + text + roast_flow.title_suffix()


def _refresh_title() -> None:
    aw = _state.aw
    if aw is None:
        return
    try:
        aw.updateWindowTitle()
    except Exception as e: # pylint: disable=broad-except
        _log.exception(e)


def _on_lease_state(state:LeaseState, reason:str) -> None:
    manager = _state.manager
    if _state.banner is not None and manager is not None:
        _state.banner.apply(state, manager.roaster_name, reason)
    _refresh_title()


def _on_batch_changed() -> None:
    """The roast batch changed (started, finished, cancelled): the banner and the window title say which one is running."""
    if _state.banner is not None:
        _state.banner.set_batch(roast_flow.banner_text())
    _show_pending_files() # files were just queued when a batch is finished
    _refresh_title()


def _capacity_kg(roaster:Roaster) -> float|None:
    """The batch capacity of a roaster in kg (the ERP stores it as text with its unit); None if unknown or not in kg."""
    if not roaster.capacity or (roaster.capacity_unit or 'kg').strip().lower() != 'kg':
        return None
    try:
        value = float(roaster.capacity)
    except ValueError:
        return None
    return value if value > 0 else None


def _ensure_bridge() -> _Bridge:
    if _state.bridge is None:
        _state.bridge = _Bridge()
        _state.bridge.state_changed.connect(_on_lease_state)
    return _state.bridge


def _show_pending_files() -> None:
    if _state.banner is not None and _state.queue is not None:
        _state.banner.set_pending_files(_state.queue.pending_count())


def _on_upload_event(event:str, job:Job|None, reason:str) -> None:
    """The sender told something (GUI thread): the banner counts what is still to send, a sent file is mentioned, a refused one is explained."""
    _show_pending_files()
    aw = _state.aw
    if aw is None:
        return
    if event == attachments.SENT and job is not None:
        try:
            aw.sendmessage(QApplication.translate('ERP', 'Đã gửi tệp {0} lên ERP').format(job.filename))
        except Exception as e: # pylint: disable=broad-except
            _log.exception(e)
    elif event == attachments.REJECTED and job is not None:
        box = QMessageBox(aw)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle('ERP')
        box.setText(QApplication.translate('ERP', 'ERP không nhận tệp {0}.').format(job.filename))
        box.setInformativeText(QApplication.translate(
            'ERP', '{0}\n\nTệp vẫn được giữ lại trên máy này, trong thư mục: {1}').format(reason, _state.queue.rejected_directory if _state.queue else ''))
        box.exec()


def _start_uploads() -> None:
    """Starts the sender of the files of finished batches. Failing to start it never keeps the user out: the files are then simply not sent."""
    if _state.uploader is not None:
        return
    try:
        queue = UploadQueue()
        uploader = Uploader(queue)
        bridge = _ensure_bridge()
        bridge.upload_event.connect(_on_upload_event)
        uploader.add_listener(bridge.upload_event.emit)
        _state.queue, _state.uploader = queue, uploader
        roast_flow.set_uploads(queue, uploader)
        uploader.start()
        _show_pending_files()
    except Exception as e: # pylint: disable=broad-except
        _log.exception(e)


def _stop_uploads() -> None:
    uploader = _state.uploader
    _state.uploader = None
    roast_flow.set_uploads(None, None)
    if uploader is not None:
        uploader.stop(wait=False)


def _resume_uploads() -> None:
    """The ERP login is valid again: whatever waited for it is sent now."""
    if _state.uploader is not None:
        _state.uploader.resume()


def _adopt(roaster:Roaster, claim:Claim) -> None:
    """Starts keeping the freshly claimed roaster alive (and gives back a previous one, if any)."""
    previous = _state.manager
    if previous is not None:
        previous.stop(release=previous.is_ok)
    display = f'{roaster.name} ({roaster.code})' if roaster.name and roaster.code else (roaster.name or roaster.code)
    manager = LeaseManager(roaster.id, display, claim, session_id=device.session_id())
    bridge = _state.bridge
    if bridge is not None:
        manager.add_listener(bridge.state_changed.emit)
    _state.manager = manager
    _state.capacity_kg = _capacity_kg(roaster)
    manager.start()
    if _state.banner is not None:
        _state.banner.apply(LeaseState.HELD, manager.roaster_name, '')
        _state.banner.set_batch(roast_flow.banner_text())
    _refresh_title()


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
            _start_uploads() # files of batches finished earlier (maybe before a crash) are sent from now on
            roast_flow.resume_if_any(aw, _state.manager) # a batch left running by a crash is offered for finishing or cancelling
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
    realtime = _state.realtime
    _state.realtime = None
    _stop_uploads() # files still queued stay on disk and are sent at the next start
    if manager is None and realtime is None:
        return

    def work() -> None:
        if realtime is not None:
            realtime.stop() # waits for its thread
        if manager is not None:
            manager.stop() # the last heartbeat, then the release call to the ERP

    try:
        background.call_with_wait(_state.aw, QApplication.translate('ERP', 'Đang trả máy rang cho ERP…'), work, timeout=timeout)
    except background.Cancelled:
        _log.info('the ERP did not confirm the release in time: the lease expires on its own')
    except Exception as e: # pylint: disable=broad-except
        _log.info('could not release the roaster: %s', type(e).__name__)


# ---- the roast batch: what Artisan calls -------------------------------------------------------------------------------------

def before_start(aw:'ApplicationWindow') -> bool:
    """START was pressed: the operator picks the production command and the ERP creates the batch. False: do not start recording."""
    return roast_flow.before_start(aw, _state.manager, capacity_kg=_state.capacity_kg, relogin=lambda: require_login(aw))


def recording_started(aw:'ApplicationWindow') -> None:
    roast_flow.recording_started(aw)


def roast_stopped(aw:'ApplicationWindow') -> None:
    roast_flow.roast_stopped(aw)


def before_reset(aw:'ApplicationWindow') -> bool:
    """RESET was pressed. False: do not reset (a batch is still open on the ERP and the operator kept it)."""
    return roast_flow.before_reset(aw)


def confirm_quit(aw:'ApplicationWindow') -> bool:
    """Artisan is about to close. False: stay (a batch is still open on the ERP)."""
    if _state.quit_confirmed:
        return True
    return roast_flow.confirm_quit(aw)


def profile_link(aw:'ApplicationWindow') -> dict[str, Any]:
    return roast_flow.profile_link(aw)
