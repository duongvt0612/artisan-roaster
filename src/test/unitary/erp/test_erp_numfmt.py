"""Định dạng số của phần ERP (chủ dự án chốt 05/11/2026): dấu "." thập phân, dấu "," ngăn nghìn, không theo locale của máy;
ô số chặn gõ dấu "," và chặn dán (cùng quy ước với ô số trên web ERP)."""

from collections.abc import Generator

import pytest
from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDoubleSpinBox

from erp import numfmt


@pytest.fixture(scope='module')
def qapp() -> Generator[QApplication, None, None]:
    yield QApplication.instance() or QApplication([])  # type:ignore[misc]


def _spin(decimals:int = 3) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setDecimals(decimals)
    spin.setRange(0.0, 100000.0)
    numfmt.guard_number_spin(spin)
    return spin


class TestFormatNumber:
    def test_thousands_comma_decimal_point_trim_zeros(self, qapp:QApplication) -> None:  # noqa: ARG002
        assert numfmt.format_number(1234.5, 4) == '1,234.5'
        assert numfmt.format_number(1234567.891, 3) == '1,234,567.891'
        assert numfmt.format_number(120.0, 4) == '120'
        assert numfmt.format_number(0.0004, 4) == '0.0004'
        assert numfmt.format_number(0.00004, 4) == '0'
        assert numfmt.format_number(-2.5, 1) == '-2.5'

    def test_does_not_follow_the_machine_locale(self, qapp:QApplication) -> None:  # noqa: ARG002
        old = QLocale()
        try:
            QLocale.setDefault(QLocale(QLocale.Language.Vietnamese, QLocale.Country.Vietnam))
            assert numfmt.format_number(1234.5, 3) == '1,234.5'
        finally:
            QLocale.setDefault(old)


class TestGuardNumberSpin:
    def test_shows_comma_thousands_and_point_decimals(self, qapp:QApplication) -> None:  # noqa: ARG002
        spin = _spin()
        spin.setValue(12345.678)
        assert spin.lineEdit().text() == '12,345.678'

    def test_typed_comma_is_ignored(self, qapp:QApplication) -> None:  # noqa: ARG002
        spin = _spin()
        spin.lineEdit().clear()
        QTest.keyClicks(spin.lineEdit(), '1,5')
        assert spin.lineEdit().text() == '15'  # phím "," không vào ô
        spin.interpretText()
        assert spin.value() == 15.0

    def test_point_is_the_decimal_separator_even_on_a_vietnamese_machine(self, qapp:QApplication) -> None:  # noqa: ARG002
        old = QLocale()
        try:
            QLocale.setDefault(QLocale(QLocale.Language.Vietnamese, QLocale.Country.Vietnam))
            spin = _spin()
            spin.lineEdit().clear()
            QTest.keyClicks(spin.lineEdit(), '30.5')
            spin.interpretText()
            assert spin.value() == 30.5
        finally:
            QLocale.setDefault(old)

    def test_paste_shortcut_is_blocked(self, qapp:QApplication) -> None:
        spin = _spin()
        spin.setValue(1.0)
        qapp.clipboard().setText('999')
        spin.lineEdit().selectAll()
        QTest.keyClick(spin.lineEdit(), Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(spin.lineEdit(), Qt.Key.Key_V, Qt.KeyboardModifier.MetaModifier)
        QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Insert, Qt.KeyboardModifier.ShiftModifier)
        spin.interpretText()
        assert spin.value() == 1.0

    def test_no_context_menu_and_no_drop(self, qapp:QApplication) -> None:  # noqa: ARG002
        spin = _spin()
        assert spin.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu
        assert spin.lineEdit().contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu
        assert not spin.lineEdit().acceptDrops()
