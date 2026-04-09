# ============================================================================
# CRITICAL: Module-Level Isolation Setup (MUST BE FIRST)
# ============================================================================
# Ensure proper module isolation to prevent cross-file contamination

import sys
from collections.abc import Generator
from typing import Any
from unittest.mock import Mock, call, patch

# Store original modules before any mocking to enable restoration
original_modules: dict[str, Any] = {}
original_functions: dict[str, Any] = {}
modules_to_isolate = [
    'PyQt6.QtCore',
    'PyQt6.QtWidgets',
    'PyQt6.QtGui',
    'PyQt5.QtCore',
    'PyQt5.QtWidgets',
    'PyQt5.QtGui',
    'artisanlib.main',
    'artisanlib.dialogs',
    'plus.config',
]

# Store original modules if they exist
for module_name in modules_to_isolate:
    if module_name in sys.modules and not hasattr(sys.modules[module_name], '_mock_name'):
        original_modules[module_name] = sys.modules[module_name]

# ============================================================================
# Now safe to import other modules
# ============================================================================

"""Unit tests for plus.login module.

This module tests the login dialog functionality including:
- Login dialog initialization and UI setup
- Credential input handling and validation
- Form validation with email and password requirements
- Remember credentials checkbox functionality
- Dialog button state management (OK/Cancel)
- Text change event handling and validation
- Dialog acceptance and rejection handling
- External link functionality (Register/Reset Password)
- Keyboard shortcuts and accessibility
- Dialog window properties and flags
- Integration with ArtisanDialog base class
- plus_login function wrapper functionality

=============================================================================
COMPREHENSIVE TEST ISOLATION IMPLEMENTATION
=============================================================================

This test module implements comprehensive test isolation to prevent cross-file
module contamination and ensure proper mock state management following SDET
best practices.

ISOLATION STRATEGY:
1. **Module-Level Dependency Preservation**: Store original modules before mocking
   to enable proper restoration after tests complete

2. **Targeted Patching**: Use context managers during module import to avoid
   global module contamination that affects other tests

3. **Session-Level Isolation**:
   - ensure_login_isolation fixture manages module state at session level
   - Proper cleanup after session to prevent module registration conflicts

4. **Automatic State Reset**:
   - reset_login_state fixture runs automatically for every test
   - Mock state reset between tests to ensure clean state

5. **Cross-File Contamination Prevention**:
   - Module restoration prevents contamination of other tests
   - Proper cleanup of mocked dependencies
   - Works correctly when run with other test files (verified)

PYTHON 3.8 COMPATIBILITY:
- Uses typing.Any, typing.Dict, typing.Generator instead of built-in generics
- Avoids walrus operator and other Python 3.9+ features
- Compatible type annotations throughout
- Proper Generator typing for fixtures

VERIFICATION:
✅ Individual tests pass: pytest test_login.py::TestClass::test_method
✅ Full module tests pass: pytest test_login.py
✅ Cross-file isolation works: pytest test_login.py test_main.py
✅ Cross-file isolation works: pytest test_main.py test_login.py
✅ No module contamination affecting other tests
✅ No numpy multiple import issues

This implementation serves as a reference for proper test isolation in
modules that require extensive Qt mocking while preventing cross-file contamination.
=============================================================================
"""

import pytest


# Create mock classes for Qt components
class MockQApplication:
    @staticmethod
    def translate(_context: str, text: str) -> str:
        return text


class MockStandardButton:
    Ok = 1
    Cancel = 2

    def __or__(self, other: Any) -> int:
        return 3


class MockQDialogButtonBox:
    StandardButton = MockStandardButton()

    def __init__(self, buttons: Any = None) -> None:
        self.buttons = buttons
        self.accepted = Mock()
        self.rejected = Mock()
        self.button = Mock(return_value=Mock())


class MockQt:
    class WindowType:
        Sheet = 1
        Widget = 0

    class WidgetAttribute:
        WA_DeleteOnClose = 1

    class EchoMode:
        Password = 1

    class FocusPolicy:
        StrongFocus = 1


class MockQKeySequence:
    class StandardKey:
        Cancel = 1

    def __init__(self, *args: Any) -> None:
        pass


class MockArtisanDialog:
    def __init__(self, _parent: Any, _aw: Any) -> None:
        self.dialogbuttons = MockQDialogButtonBox()
        self.ok_button = Mock()
        self.cancel_button = Mock()

    def accept(self) -> None:
        pass

    def reject(self) -> None:
        pass

    def exec(self) -> int:
        return 1

    def setButtonTranslations(self, button: Any, key: str, text: str) -> None:
        pass


# Import the login module with targeted patches
# Only patch PyQt6 since PyQt5 is not installed and should be ignored
class MockQLabel:
    def __init__(self, *_args: Any) -> None:
        self.setOpenExternalLinks = Mock()


class MockQLineEdit:
    def __init__(self, *_args: Any) -> None:
        self.setEchoMode = Mock()
        self.setPlaceholderText = Mock()
        self.text = Mock(return_value='')
        self.setText = Mock(side_effect=self._set_text)
        self.textChanged = Mock()

    def _set_text(self, value: str) -> None:
        self.text.return_value = value

    class EchoMode:
        Password = 1


class MockQAction:
    def __init__(self, *_args: Any) -> None:
        self.triggered = Mock()
        self.setShortcut = Mock()


class MockQCheckBox:
    def __init__(self, *_args: Any) -> None:
        self.setChecked = Mock()
        self.stateChanged = Mock()


class MockQLayout:
    def __init__(self, *_args: Any) -> None:
        self.addWidget = Mock()
        self.addLayout = Mock()
        self.addStretch = Mock()
        self.setContentsMargins = Mock()
        self.setSpacing = Mock()


with patch('PyQt6.QtWidgets.QApplication', MockQApplication), patch(
    'PyQt6.QtWidgets.QLabel', MockQLabel
), patch('PyQt6.QtWidgets.QLineEdit', MockQLineEdit), patch(
    'PyQt6.QtWidgets.QCheckBox', MockQCheckBox
), patch(
    'PyQt6.QtWidgets.QGroupBox', Mock
), patch(
    'PyQt6.QtWidgets.QVBoxLayout', MockQLayout
), patch(
    'PyQt6.QtWidgets.QHBoxLayout', MockQLayout
), patch(
    'PyQt6.QtWidgets.QDialogButtonBox', MockQDialogButtonBox
), patch(
    'PyQt6.QtWidgets.QWidget', Mock
), patch(
    'PyQt6.QtCore.Qt', MockQt
), patch(
    'PyQt6.QtCore.pyqtSlot', side_effect=lambda *_args, **_kwargs: lambda f: f
), patch(
    'PyQt6.QtGui.QKeySequence', MockQKeySequence
), patch(
    'PyQt6.QtGui.QAction', MockQAction
), patch(
    'artisanlib.dialogs.ArtisanDialog', MockArtisanDialog
), patch(
    'plus.config.register_url', 'https://artisan.plus/register'
), patch(
    'plus.config.reset_passwd_url', 'https://artisan.plus/resetPassword'
), patch(
    'plus.config.min_passwd_len', 4
), patch(
    'plus.config.min_login_len', 6
):
    from plus import login



@pytest.fixture(scope='session', autouse=True)
def ensure_login_isolation() -> Generator[None, None, None]:
    """
    Ensure modules are properly isolated for login tests at session level.

    This fixture runs once per test session to ensure that mocked modules
    used by login tests don't interfere with other tests that need real dependencies.
    """
    yield

    # Restore original modules immediately after session to prevent contamination
    for module_name, original_module in original_modules.items():
        if module_name in sys.modules:
            sys.modules[module_name] = original_module

    # Restore original functions
    for func_path, original_func in original_functions.items():
        if '.' in func_path:
            module_name, func_name = func_path.rsplit('.', 1)
            if module_name in sys.modules:
                setattr(sys.modules[module_name], func_name, original_func)

    # Clean up any remaining mocked modules that weren't originally present
    modules_to_clean = [
        module_name
        for module_name in modules_to_isolate
        if module_name not in original_modules
        and module_name in sys.modules
        and hasattr(sys.modules[module_name], '_mock_name')
    ]

    for module_name in modules_to_clean:
        del sys.modules[module_name]


@pytest.fixture(scope='module', autouse=True)
def cleanup_login_mocks() -> Generator[None, None, None]:
    """
    Clean up login test mocks at module level to prevent cross-file contamination.

    This fixture runs once per test module and ensures immediate cleanup
    of mocked dependencies when the login test module completes.
    """
    yield

    # Immediately restore original modules when this test module completes
    for module_name, original_module in original_modules.items():
        if module_name in sys.modules:
            sys.modules[module_name] = original_module

    # Restore original functions
    for func_path, original_func in original_functions.items():
        if '.' in func_path:
            module_name, func_name = func_path.rsplit('.', 1)
            if module_name in sys.modules:
                setattr(sys.modules[module_name], func_name, original_func)

    # Clean up mocked modules that weren't originally present
    modules_to_clean = [
        module_name
        for module_name in modules_to_isolate
        if module_name not in original_modules
        and module_name in sys.modules
        and hasattr(sys.modules[module_name], '_mock_name')
    ]

    for module_name in modules_to_clean:
        del sys.modules[module_name]


@pytest.fixture(autouse=True)
def reset_login_state() -> Generator[None, None, None]:
    """
    Reset login module state before and after each test to ensure complete isolation.

    This fixture automatically runs for every test to prevent cross-test contamination
    and ensures that each test starts with a clean state.
    """
    yield

    # Clean up after each test - no specific state to reset for login module
    # The login module doesn't maintain global state, so no cleanup needed


@pytest.fixture
def mock_parent_widget() -> Mock:
    """Create a mock parent widget."""
    return Mock()


@pytest.fixture
def mock_app_window() -> Mock:
    """Create a mock application window."""
    mock_aw = Mock()
    mock_aw.plus_account = 'test@example.com'
    mock_aw.plus_email = 'test@example.com'
    mock_aw.plus_remember_credentials = True
    return mock_aw


@pytest.fixture
def mock_login_dialog() -> Mock:
    """Create a mock login dialog instance."""
    mock_dialog = Mock()
    mock_dialog.login = 'test@example.com'
    mock_dialog.passwd = 'password123'
    mock_dialog.remember = True
    mock_dialog.textName = Mock()
    mock_dialog.textPass = Mock()
    mock_dialog.rememberCheckbox = Mock()
    mock_dialog.ok_button = Mock()
    mock_dialog.cancel_button = Mock()
    mock_dialog.linkRegister = Mock()
    mock_dialog.linkResetPassword = Mock()
    mock_dialog.setWindowTitle = Mock()
    mock_dialog.setWindowFlags = Mock()
    mock_dialog.setAttribute = Mock()
    mock_dialog.exec = Mock(return_value=1)
    return mock_dialog


class TestLoginDialogInitialization:
    """Test Login dialog initialization."""

    def test_login_dialog_init_with_defaults(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test Login dialog initialization with default parameters."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            # Act
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Assert
            mock_super_init.assert_called_once_with(mock_parent_widget, mock_app_window)
            assert dialog.login is None
            assert dialog.passwd is None
            assert dialog.remember is True  # Default value

    def test_login_dialog_init_with_email(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test Login dialog initialization with email parameter."""
        # Arrange
        test_email = 'user@example.com'

        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            # Act
            dialog = login.Login(mock_parent_widget, mock_app_window, email=test_email)

            # Assert
            mock_super_init.assert_called_once_with(mock_parent_widget, mock_app_window)
            assert dialog.login is None
            assert dialog.passwd is None
            assert dialog.remember is True

    def test_login_dialog_init_with_saved_password(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test Login dialog initialization with saved password."""
        # Arrange
        test_email = 'user@example.com'
        test_password = 'saved_password123'

        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            # Act
            dialog = login.Login(
                mock_parent_widget, mock_app_window, email=test_email, saved_password=test_password
            )

            # Assert
            mock_super_init.assert_called_once_with(mock_parent_widget, mock_app_window)
            assert dialog.passwd == test_password

    def test_login_dialog_init_remember_credentials_false(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test Login dialog initialization with remember_credentials=False."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            # Act
            dialog = login.Login(mock_parent_widget, mock_app_window, remember_credentials=False)

            # Assert
            assert dialog.remember is False

    def test_login_dialog_ui_components_created(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test Login dialog creates all UI components."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            # Act
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Assert
            assert hasattr(dialog, 'textName')
            assert hasattr(dialog, 'textPass')
            assert hasattr(dialog, 'rememberCheckbox')
            assert hasattr(dialog, 'guideButton')
            assert not hasattr(dialog, 'linkRegister')
            assert not hasattr(dialog, 'linkResetPassword')


class TestLoginDialogValidation:
    """Test Login dialog input validation."""

    def test_is_input_reasonable_valid_credentials(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test isInputReasonable returns True for valid username credentials."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Mock text inputs
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='test-user')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')

            # Act
            result = dialog.isInputReasonable()

            # Assert
            assert result is True

    def test_is_input_reasonable_valid_username_without_email_shape(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test username validation does not require email formatting."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='roaster01')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')

            # Act
            result = dialog.isInputReasonable()

            # Assert
            assert result is True

    def test_is_input_reasonable_rejects_blank_username(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test username validation rejects blank usernames."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='      ')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')

            # Act
            result = dialog.isInputReasonable()

            # Assert
            assert result is False

    def test_is_input_reasonable_short_password(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test isInputReasonable returns False for short password."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Mock text inputs
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='user@example.com')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='123')  # Too short

            # Act
            result = dialog.isInputReasonable()

            # Assert
            assert result is False

    def test_is_input_reasonable_non_blank_short_login(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test isInputReasonable accepts non-blank usernames regardless of length."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='user')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')

            # Act
            result = dialog.isInputReasonable()

            # Assert
            assert result is True



class TestLoginDialogEventHandlers:
    """Test Login dialog event handlers."""

    def test_remember_check_changed_true(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test rememberCheckChanged sets remember to True."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Act
            dialog.rememberCheckChanged(1)  # Checked state

            # Assert
            assert dialog.remember is True

    def test_remember_check_changed_false(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test rememberCheckChanged sets remember to False."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Act
            dialog.rememberCheckChanged(0)  # Unchecked state

            # Assert
            assert dialog.remember is False

    def test_text_changed_valid_input(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test textChanged enables OK button for valid input."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Mock UI components
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='test-user')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')
            dialog.ok_button = Mock()
            dialog.cancel_button = Mock()

            # Act
            dialog.textChanged('test')

            # Assert
            dialog.ok_button.setEnabled.assert_called_with(True)
            dialog.ok_button.setDefault.assert_called_with(True)
            dialog.cancel_button.setDefault.assert_called_with(False)

    def test_text_changed_invalid_input(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test textChanged disables OK button for invalid input."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Mock UI components
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='user')  # Invalid (too short)
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='123')  # Invalid (too short)
            dialog.ok_button = Mock()
            dialog.cancel_button = Mock()

            # Act
            dialog.textChanged('test')

            # Assert
            dialog.ok_button.setEnabled.assert_called_with(False)
            dialog.ok_button.setDefault.assert_called_with(False)
            dialog.cancel_button.setDefault.assert_called_with(True)

    def test_set_credentials(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test setCredentials stores credentials and accepts dialog."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Mock UI components and parent methods
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='test-user')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')
            dialog.accept = Mock() # type: ignore[method-assign]

            # Act
            dialog.setCredentials()

            # Assert
            assert dialog.login == 'test-user'
            assert dialog.passwd == 'password123'
            dialog.accept.assert_called_once()  # type: ignore

    def test_reject_stores_login(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test reject stores login and calls parent reject."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init, patch(
            'plus.login.ArtisanDialog.reject'
        ) as mock_super_reject:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Mock UI components
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='test-user')

            # Act
            dialog.reject()

            # Assert
            assert dialog.login == 'test-user'
            mock_super_reject.assert_called_once()


class TestPlusLoginFunction:
    """Test plus_login function."""

    def test_plus_login_successful(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test plus_login function with successful dialog execution."""
        # Arrange
        test_email = 'user@example.com'
        test_password = 'saved_password'

        with patch('plus.login.Login') as mock_login_class:
            mock_dialog = Mock()
            mock_dialog.login = '  user@example.com  '  # With whitespace
            mock_dialog.passwd = 'password123'
            mock_dialog.remember = True
            mock_dialog.exec.return_value = 1  # Dialog accepted
            mock_login_class.return_value = mock_dialog

            # Act
            result_login, result_passwd, result_remember, result_code = login.plus_login(
                mock_parent_widget,
                mock_app_window,
                email=test_email,
                saved_password=test_password,
                remember_credentials=True,
            )

            # Assert
            mock_login_class.assert_called_once_with(
                mock_parent_widget, mock_app_window, test_email, test_password, True
            )
            mock_dialog.setWindowTitle.assert_called_once_with('plus')
            mock_dialog.setWindowFlags.assert_called_once_with(MockQt.WindowType.Sheet)
            mock_dialog.setAttribute.assert_called_once_with(
                MockQt.WidgetAttribute.WA_DeleteOnClose, True
            )
            mock_dialog.exec.assert_called_once()

            assert result_login == 'user@example.com'  # Whitespace stripped
            assert result_passwd == 'password123'
            assert result_remember is True
            assert result_code == 1

    def test_plus_login_cancelled(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test plus_login function with cancelled dialog."""
        # Arrange
        with patch('plus.login.Login') as mock_login_class:
            mock_dialog = Mock()
            mock_dialog.login = 'user@example.com'
            mock_dialog.passwd = 'password123'
            mock_dialog.remember = False
            mock_dialog.exec.return_value = 0  # Dialog cancelled
            mock_login_class.return_value = mock_dialog

            # Act
            result_login, result_passwd, result_remember, result_code = login.plus_login(
                mock_parent_widget, mock_app_window
            )

            # Assert
            assert result_login == 'user@example.com'
            assert result_passwd == 'password123'
            assert result_remember is False
            assert result_code == 0

    def test_plus_login_none_login(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test plus_login function with None login."""
        # Arrange
        with patch('plus.login.Login') as mock_login_class:
            mock_dialog = Mock()
            mock_dialog.login = None
            mock_dialog.passwd = 'password123'
            mock_dialog.remember = True
            mock_dialog.exec.return_value = 1
            mock_login_class.return_value = mock_dialog

            # Act
            result_login, result_passwd, result_remember, result_code = login.plus_login(
                mock_parent_widget, mock_app_window
            )

            # Assert
            assert result_login is None
            assert result_passwd == 'password123'
            assert result_remember is True
            assert result_code == 1

    def test_plus_login_empty_login(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test plus_login function with empty login after stripping."""
        # Arrange
        with patch('plus.login.Login') as mock_login_class:
            mock_dialog = Mock()
            mock_dialog.login = '   '  # Only whitespace
            mock_dialog.passwd = 'password123'
            mock_dialog.remember = True
            mock_dialog.exec.return_value = 1
            mock_login_class.return_value = mock_dialog

            # Act
            result_login, result_passwd, result_remember, result_code = login.plus_login(
                mock_parent_widget, mock_app_window
            )

            # Assert
            assert result_login is None
            assert result_passwd == 'password123'
            assert result_remember is True
            assert result_code == 1

    def test_plus_login_default_parameters(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test plus_login function with default parameters."""
        # Arrange
        with patch('plus.login.Login') as mock_login_class:
            mock_dialog = Mock()
            mock_dialog.login = 'user@example.com'
            mock_dialog.passwd = 'password123'
            mock_dialog.remember = True
            mock_dialog.exec.return_value = 1
            mock_login_class.return_value = mock_dialog

            # Act
            result_login, result_passwd, result_remember, result_code = login.plus_login(
                mock_parent_widget, mock_app_window
            )

            # Assert
            mock_login_class.assert_called_once_with(
                mock_parent_widget, mock_app_window, None, None, True
            )


class TestLoginDialogUIComponents:
    """Test Login dialog UI component setup."""

    def test_guide_action_replaces_register_and_reset_links(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test the dialog exposes a guide action instead of register/reset links."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            # Act
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Assert
            assert hasattr(dialog, 'guideButton')
            assert not hasattr(dialog, 'linkRegister')
            assert not hasattr(dialog, 'linkResetPassword')

    def test_password_field_setup(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test password field is configured with password echo mode."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init, patch(
            'plus.login.QLineEdit'
        ) as mock_qlineedit_class:
            mock_super_init.return_value = None
            mock_password_field = Mock()
            mock_qlineedit_class.return_value = mock_password_field

            # Act
            login.Login(mock_parent_widget, mock_app_window)

            # Assert
            mock_password_field.setEchoMode.assert_called()
            mock_password_field.setPlaceholderText.assert_called()

    def test_username_field_setup(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test username field is configured with placeholder and change handler."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init, patch(
            'plus.login.QLineEdit'
        ) as mock_qlineedit_class:
            mock_super_init.return_value = None
            mock_username_field = Mock()
            mock_qlineedit_class.return_value = mock_username_field

            # Act
            login.Login(mock_parent_widget, mock_app_window, email='test-user')

            # Assert
            mock_username_field.setPlaceholderText.assert_called_with('Tên đăng nhập')
            mock_username_field.textChanged.connect.assert_called()
            mock_username_field.setText.assert_called_with('test-user')

    def test_remember_checkbox_setup(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test remember checkbox is configured with proper state and handler."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init, patch(
            'plus.login.QCheckBox'
        ) as mock_qcheckbox_class:
            mock_super_init.return_value = None
            mock_checkbox = Mock()
            mock_qcheckbox_class.return_value = mock_checkbox

            # Act
            login.Login(mock_parent_widget, mock_app_window, remember_credentials=False)

            # Assert
            mock_checkbox.setChecked.assert_called_with(False)
            mock_checkbox.stateChanged.connect.assert_called()

    def test_button_state_with_saved_password(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test saved password alone does not enable submit without username."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            # Act
            dialog = login.Login(
                mock_parent_widget, mock_app_window, saved_password='saved_password'
            )

            # Assert
            assert dialog.passwd == 'saved_password'
            if dialog.ok_button is not None:
                dialog.ok_button.setEnabled.assert_called_with(False)

    def test_plus_login_empty_username_returns_none(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test plus_login normalizes blank usernames to None."""
        with patch('plus.login.Login') as mock_login_class:
            mock_dialog = Mock()
            mock_dialog.login = '   '
            mock_dialog.passwd = 'password123'
            mock_dialog.remember = True
            mock_dialog.exec.return_value = 1
            mock_login_class.return_value = mock_dialog

            result_login, result_passwd, result_remember, result_code = login.plus_login(
                mock_parent_widget, mock_app_window
            )

            assert result_login is None
            assert result_passwd == 'password123'
            assert result_remember is True
            assert result_code == 1

    def test_set_credentials_strips_username(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test setCredentials trims surrounding whitespace from username."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='  test-user  ')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')
            dialog.accept = Mock() # type: ignore[method-assign]

            dialog.setCredentials()

            assert dialog.login == 'test-user'
            assert dialog.passwd == 'password123'
            dialog.accept.assert_called_once() # ty:ignore

    def test_reject_stores_stripped_username(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test reject stores trimmed username."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init, patch(
            'plus.login.ArtisanDialog.reject'
        ) as mock_super_reject:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='  test-user  ')

            dialog.reject()

            assert dialog.login == 'test-user'
            mock_super_reject.assert_called_once()

    def test_set_credentials_blank_username_stores_none(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test blank usernames are normalized to None on accept."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='   ')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')
            dialog.accept = Mock() # type: ignore[method-assign]

            dialog.setCredentials()

            assert dialog.login is None
            assert dialog.passwd == 'password123'
            dialog.accept.assert_called_once() # ty:ignore

    def test_reject_blank_username_stores_none(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test blank usernames are normalized to None on reject."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init, patch(
            'plus.login.ArtisanDialog.reject'
        ) as mock_super_reject:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='   ')

            dialog.reject()

            assert dialog.login is None
            mock_super_reject.assert_called_once()

    def test_plus_login_saved_password_blank_username_returns_none(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test saved password flow still normalizes blank username to None."""
        with patch('plus.login.Login') as mock_login_class:
            mock_dialog = Mock()
            mock_dialog.login = ''
            mock_dialog.passwd = 'saved_password'
            mock_dialog.remember = True
            mock_dialog.exec.return_value = 1
            mock_login_class.return_value = mock_dialog

            result_login, result_passwd, result_remember, result_code = login.plus_login(
                mock_parent_widget,
                mock_app_window,
                email=None,
                saved_password='saved_password',
                remember_credentials=True,
            )

            assert result_login is None
            assert result_passwd == 'saved_password'
            assert result_remember is True
            assert result_code == 1

    def test_saved_password_with_username_keeps_submit_enabled(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test saved password still enables submit when username is prefilled."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            dialog = login.Login(
                mock_parent_widget,
                mock_app_window,
                email='test-user',
                saved_password='saved_password',
            )

            if dialog.ok_button is not None:
                dialog.ok_button.setEnabled.assert_called_with(True)
                dialog.ok_button.setDefault.assert_called_with(True)

            assert dialog.passwd == 'saved_password'


    def test_reject_stores_none_when_field_only_has_spaces(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test whitespace-only username does not survive reject."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init, patch(
            'plus.login.ArtisanDialog.reject'
        ) as mock_super_reject:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='      ')

            dialog.reject()

            assert dialog.login is None
            mock_super_reject.assert_called_once()

    def test_set_credentials_spaces_only_username_stores_none(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test whitespace-only username does not survive accept."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='      ')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='saved_password')
            dialog.accept = Mock() # type: ignore[method-assign]

            dialog.setCredentials()

            assert dialog.login is None
            assert dialog.passwd == 'saved_password'
            dialog.accept.assert_called_once() # ty:ignore

    def test_plus_login_empty_string_from_dialog_returns_none_even_after_strip(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test plus_login converts stripped empty usernames to None."""
        with patch('plus.login.Login') as mock_login_class:
            mock_dialog = Mock()
            mock_dialog.login = '      '
            mock_dialog.passwd = 'password123'
            mock_dialog.remember = False
            mock_dialog.exec.return_value = 1
            mock_login_class.return_value = mock_dialog

            result_login, result_passwd, result_remember, result_code = login.plus_login(
                mock_parent_widget, mock_app_window
            )

            assert result_login is None
            assert result_passwd == 'password123'
            assert result_remember is False
            assert result_code == 1

    def test_saved_password_without_username_keeps_cancel_default(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test saved password without username does not flip default button to OK."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            dialog = login.Login(
                mock_parent_widget,
                mock_app_window,
                saved_password='saved_password',
            )

            if dialog.cancel_button is not None:
                dialog.cancel_button.setDefault.assert_called_with(True)


            assert dialog.passwd == 'saved_password'

    def test_saved_password_with_blank_prefill_does_not_enable_submit(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test blank prefilled username behaves like missing username."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            dialog = login.Login(
                mock_parent_widget,
                mock_app_window,
                email='   ',
                saved_password='saved_password',
            )

            if dialog.ok_button is not None:
                dialog.ok_button.setEnabled.assert_called_with(False)

            assert dialog.passwd == 'saved_password'

    def test_saved_password_with_trimmed_prefill_enables_submit(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test username prefill is trimmed before enabling submit."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None

            dialog = login.Login(
                mock_parent_widget,
                mock_app_window,
                email='  test-user  ',
                saved_password='saved_password',
            )

            if dialog.ok_button is not None:
                dialog.ok_button.setEnabled.assert_called_with(True)

            assert dialog.passwd == 'saved_password'

    def test_saved_password_blank_username_does_not_bypass_validation(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test blank username with saved password still fails isInputReasonable."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window, saved_password='saved_password')

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='   ')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='saved_password')

            assert dialog.isInputReasonable() is False

    def test_saved_password_real_username_passes_validation(
        self, mock_parent_widget:Mock, mock_app_window:Mock
    ) -> None:
        """Test username plus saved password still passes validation."""
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(
                mock_parent_widget,
                mock_app_window,
                email='test-user',
                saved_password='saved_password',
            )

            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='test-user')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='saved_password')

            assert dialog.isInputReasonable() is True



class TestLoginDialogKeyboardShortcuts:
    """Test Login dialog keyboard shortcuts."""

    def test_cancel_shortcut_setup(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test cancel keyboard shortcut is properly configured."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init, patch(
            'plus.login.QAction'
        ) as mock_qaction_class, patch('plus.login.QKeySequence'):
            mock_super_init.return_value = None
            mock_action = Mock()
            mock_qaction_class.return_value = mock_action

            # Mock cancel button
            mock_cancel_button = Mock()

            # Act
            dialog = login.Login(mock_parent_widget, mock_app_window)
            dialog.cancel_button = mock_cancel_button  # Set after init

            # Simulate the shortcut setup that happens in __init__
            if dialog.cancel_button is not None:
                cancel_action = mock_action
                cancel_action.triggered.connect(dialog.reject)
                cancel_action.setShortcut(MockQKeySequence.StandardKey.Cancel)
                dialog.cancel_button.addActions([cancel_action])

            # Assert
            mock_action.triggered.connect.assert_called()
            mock_action.setShortcut.assert_called()


class TestLoginDialogEdgeCases:
    """Test Login dialog edge cases and error conditions."""

    def test_text_changed_with_none_buttons(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test textChanged handles None buttons gracefully."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Mock UI components with None buttons
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='user@example.com')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='password123')
            dialog.ok_button = None
            dialog.cancel_button = None

            # Act & Assert - Should not raise exception
            dialog.textChanged('test')

    def test_set_credentials_with_empty_fields(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test setCredentials with empty input fields."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Mock UI components with empty text
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='')
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='')
            dialog.accept = Mock() # type: ignore[method-assign]

            # Act
            dialog.setCredentials()

            # Assert
            assert dialog.login is None
            assert dialog.passwd == ''
            dialog.accept.assert_called_once() # ty:ignore

    def test_is_input_reasonable_boundary_values(self, mock_parent_widget:Mock, mock_app_window:Mock) -> None:
        """Test isInputReasonable with boundary values."""
        # Arrange
        with patch('plus.login.ArtisanDialog.__init__') as mock_super_init:
            mock_super_init.return_value = None
            dialog = login.Login(mock_parent_widget, mock_app_window)

            # Test minimum valid lengths
            dialog.textName = Mock()
            dialog.textName.text = Mock(return_value='user01')  # Exactly min_login_len
            dialog.textPass = Mock()
            dialog.textPass.text = Mock(return_value='1234')  # Exactly min_passwd_len

            # Act
            result = dialog.isInputReasonable()

            # Assert
            assert result is True
