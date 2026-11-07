#!/usr/bin/python
#
# numfmt.py
#
# ABOUT
# How the ERP part of Artisan writes and reads numbers (decided by the ERP owner on 05/11/2026, same rule as the ERP web):
# "." is the decimal separator and "," groups the thousands ("1,234.5"), whatever the Region setting of the machine is.
# A number box shows the "," groups, ignores a typed "," (it is NOT a decimal separator) and refuses paste / drag-and-drop,
# so that the kg sent to the ERP is always what the operator typed with the keys.

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

from typing import Final, override

from PyQt6.QtCore import QEvent, QLocale, QObject, Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence, QMouseEvent
from PyQt6.QtWidgets import QAbstractSpinBox


def number_locale() -> QLocale:
    """The fixed locale of every ERP number: English / United States gives "," thousands and "." decimals."""
    return QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)


def format_number(value:float, decimals:int) -> str:
    """'1,234.5', '0.0004', '120': rounded to `decimals`, trailing zeros (and a lone ".") dropped, "," thousands."""
    locale = number_locale()
    text = locale.toString(float(round(value, decimals)), 'f', decimals)
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    if text == '-0':
        return '0'
    return text


class _NumberGuard(QObject):
    """Event filter of a number box: drops the "," key and every paste (shortcut, middle click, drag-and-drop)."""

    @override
    def eventFilter(self, a0:QObject|None, a1:QEvent|None) -> bool:  # pylint: disable=invalid-name
        if a1 is None:
            return False
        kind = a1.type()
        if kind == QEvent.Type.KeyPress and isinstance(a1, QKeyEvent):
            if a1.text() == ',' or a1.matches(QKeySequence.StandardKey.Paste) or _is_paste_shortcut(a1):
                return True
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return True
        if isinstance(a1, QMouseEvent) and a1.button() == Qt.MouseButton.MiddleButton:
            return True  # X11 selection paste (middle click)
        return False


_PASTE_KEYS: Final[tuple[tuple[Qt.Key, Qt.KeyboardModifier], ...]] = (
    (Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier),
    (Qt.Key.Key_V, Qt.KeyboardModifier.MetaModifier),
    (Qt.Key.Key_Insert, Qt.KeyboardModifier.ShiftModifier),
)


def _is_paste_shortcut(event:QKeyEvent) -> bool:
    """Ctrl+V, Cmd+V (Meta on macOS when Ctrl/Meta are swapped) and Shift+Insert, also when the platform map misses one."""
    mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
    return any(event.key() == key and mods == mod for key, mod in _PASTE_KEYS)


def guard_number_spin(spin:QAbstractSpinBox) -> QAbstractSpinBox:
    """Makes `spin` follow the ERP number rule: fixed en-US locale with "," groups shown, typed "," ignored, no paste."""
    spin.setLocale(number_locale())
    spin.setGroupSeparatorShown(True)
    spin.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    spin.setAcceptDrops(False)
    guard = _NumberGuard(spin)
    spin.installEventFilter(guard)
    edit = spin.lineEdit()
    if edit is not None:
        edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        edit.setAcceptDrops(False)
        edit.installEventFilter(guard)
    return spin
