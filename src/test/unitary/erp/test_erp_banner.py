"""Unit tests for erp.banner: the strip at the top of the main window that shows which roaster this session holds."""

import os
from collections.abc import Generator

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtGui import QPalette  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from erp import banner  # noqa: E402
from erp.lease import LeaseState  # noqa: E402


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


@pytest.fixture
def bar(qapp:QApplication) -> Generator[banner.LeaseBanner, None, None]:  # noqa: ARG001
    parent = QWidget()
    b = banner.LeaseBanner(parent)
    parent.show()
    yield b
    parent.close()


def test_held_shows_which_roaster_without_alarm(bar:banner.LeaseBanner) -> None:
    bar.apply(LeaseState.HELD, 'Máy Rang Nhỏ', '')
    assert bar.isVisible()
    assert 'Máy Rang Nhỏ' in bar.label.text()
    assert bar.kind == 'held'
    assert bar.reselect_button.isHidden()


def test_suspect_warns_about_the_connection(bar:banner.LeaseBanner) -> None:
    bar.apply(LeaseState.SUSPECT, 'Máy Rang Nhỏ', 'no_connection')
    assert bar.isVisible() and bar.kind == 'suspect'
    assert 'ERP' in bar.label.text() and 'Máy Rang Nhỏ' in bar.label.text()
    assert bar.reselect_button.isHidden()


@pytest.mark.parametrize(('reason', 'fragment'), [
    ('lease_lost', 'phiên khác'),
    ('force_released', 'ép nhả'),
    ('session_expired', 'đăng nhập'),
    ('not_found', 'không còn'),
    ('forbidden', 'quyền'),
    ('something_new', 'Máy Rang Nhỏ'),   # unknown reasons still produce a readable message
])
def test_lost_says_why_and_offers_to_pick_again(bar:banner.LeaseBanner, reason:str, fragment:str) -> None:
    bar.apply(LeaseState.LOST, 'Máy Rang Nhỏ', reason)
    assert bar.isVisible() and bar.kind == 'lost'
    assert fragment in bar.label.text()
    assert not bar.reselect_button.isHidden()
    assert 'dữ liệu' in bar.label.text().lower()  # reassure: what is being recorded is not thrown away


def test_reselect_button_emits_the_signal(bar:banner.LeaseBanner) -> None:
    got:list[bool] = []
    bar.reselect.connect(lambda: got.append(True))
    bar.apply(LeaseState.LOST, 'Máy', 'lease_lost')
    bar.reselect_button.click()
    assert got == [True]


def test_idle_hides_the_banner(bar:banner.LeaseBanner) -> None:
    bar.apply(LeaseState.HELD, 'Máy', '')
    bar.apply(LeaseState.IDLE, 'Máy', '')
    assert not bar.isVisible()


def test_recovering_goes_back_to_the_quiet_held_look(bar:banner.LeaseBanner) -> None:
    bar.apply(LeaseState.SUSPECT, 'Máy', 'no_connection')
    bar.apply(LeaseState.HELD, 'Máy', 'recovered')
    assert bar.kind == 'held' and 'Máy' in bar.label.text()


def test_install_puts_the_banner_on_top_of_the_layout(qapp:QApplication) -> None:  # noqa: ARG001
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.addWidget(QLabel('graph'))
    b = banner.install(host)
    assert layout.itemAt(0).widget() is b
    assert banner.install(host) is b   # installing twice does not stack banners


def test_switching_state_restyles_the_text_too_not_only_the_background(bar:banner.LeaseBanner) -> None:
    """Regression: after HELD -> LOST the frame turned red but the text kept the 'held' colour (children were not restyled)."""
    bar.apply(LeaseState.HELD, 'Máy', '')
    held_colour = bar.label.palette().color(QPalette.ColorRole.WindowText).name()
    bar.apply(LeaseState.LOST, 'Máy', 'lease_lost')
    lost_colour = bar.label.palette().color(QPalette.ColorRole.WindowText).name()
    assert held_colour != lost_colour
    bar.apply(LeaseState.HELD, 'Máy', 'recovered')
    assert bar.label.palette().color(QPalette.ColorRole.WindowText).name() == held_colour


def _pixel(widget:QWidget, x:int, y:int) -> str:
    return widget.grab().toImage().pixelColor(x, y).name()


@pytest.mark.parametrize('state', [LeaseState.HELD, LeaseState.SUSPECT, LeaseState.LOST])
def test_text_stays_readable_inside_artisans_wildcard_stylesheet(qapp:QApplication, state:LeaseState) -> None:  # noqa: ARG001
    """Regression: Artisan paints its main widget with `* {background-color: <canvas>; color: ...}`. The banner label then got a white
    background and, with white text, was invisible until selected with the mouse. The label must keep the banner's own background."""
    host = QWidget()
    host.setStyleSheet('* {background-color: #ffffff; color: #000000; border-width: 0px;}')  # what ApplicationWindow does
    host.resize(760, 140)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    bar = banner.LeaseBanner(host)
    layout.addWidget(bar)
    layout.addStretch()
    bar.apply(state, 'Máy Rang Nhỏ (ROAST-01)', 'lease_lost')
    host.show()
    qapp.processEvents()
    frame_bg = _pixel(bar, 2, 2)                                   # the banner's own colour, at its corner
    label_bg = _pixel(bar, bar.label.x() + 1, bar.label.y() + 1)   # top-left corner of the label: no glyph is ever drawn there
    assert frame_bg != '#ffffff'
    assert label_bg == frame_bg, f'label background {label_bg} differs from the banner colour {frame_bg}'
    host.close()


class TestBatch:
    """The roast batch is shown next to the roaster, with the button that finishes it."""

    def test_no_batch_no_button(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.HELD, 'Máy Rang Nhỏ', '')
        assert bar.finish_button.isHidden() and bar.label.text() == 'Máy rang đang dùng: Máy Rang Nhỏ'

    def test_the_batch_follows_the_roaster_on_the_strip(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.HELD, 'Máy Rang Nhỏ', '')
        bar.set_batch('Đang rang LS-1 · mẻ #3 · Arabica · 40 kg')
        assert bar.label.text() == 'Máy rang đang dùng: Máy Rang Nhỏ · Đang rang LS-1 · mẻ #3 · Arabica · 40 kg'
        assert not bar.finish_button.isHidden()

    def test_the_button_asks_to_finish(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.HELD, 'Máy', '')
        bar.set_batch('Đang rang LS-1')
        got:list[int] = []
        bar.finish_batch.connect(lambda: got.append(1))
        bar.finish_button.click()
        assert got == [1]

    def test_finishing_a_batch_hides_the_button_again(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.HELD, 'Máy', '')
        bar.set_batch('Đang rang LS-1')
        bar.set_batch('')
        assert bar.finish_button.isHidden() and bar.label.text() == 'Máy rang đang dùng: Máy'

    def test_a_lost_roaster_still_allows_finishing_the_batch(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.LOST, 'Máy', 'force_released')
        bar.set_batch('Đang rang LS-1')
        assert not bar.finish_button.isHidden() and not bar.reselect_button.isHidden() and 'LS-1' in bar.label.text()

    def test_the_batch_survives_a_state_change(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.HELD, 'Máy', '')
        bar.set_batch('Đang rang LS-1')
        bar.apply(LeaseState.SUSPECT, 'Máy', 'no_connection')
        assert 'LS-1' in bar.label.text() and not bar.finish_button.isHidden()

    def test_a_batch_before_any_state_does_not_show_the_strip(self, bar:banner.LeaseBanner) -> None:
        bar.set_batch('Đang rang LS-1')
        assert not bar.isVisible()

    def test_idle_hides_everything(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.HELD, 'Máy', '')
        bar.set_batch('Đang rang LS-1')
        bar.apply(LeaseState.IDLE, 'Máy', '')
        assert not bar.isVisible()


class TestPendingFiles:
    def test_the_number_of_files_still_to_send_is_shown(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.HELD, 'Máy', '')
        bar.set_pending_files(2)
        assert bar.label.text() == 'Máy rang đang dùng: Máy · 2 tệp chưa gửi lên ERP'
        bar.set_pending_files(0)
        assert bar.label.text() == 'Máy rang đang dùng: Máy'

    def test_it_sits_next_to_the_batch(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.HELD, 'Máy', '')
        bar.set_batch('Đang rang LS-1')
        bar.set_pending_files(1)
        assert bar.label.text() == 'Máy rang đang dùng: Máy · Đang rang LS-1 · 1 tệp chưa gửi lên ERP'

    def test_it_is_kept_when_the_roaster_is_lost(self, bar:banner.LeaseBanner) -> None:
        bar.apply(LeaseState.LOST, 'Máy', 'force_released')
        bar.set_pending_files(3)
        assert '3 tệp chưa gửi lên ERP' in bar.label.text()
