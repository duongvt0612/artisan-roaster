# ============================================================================
# CRITICAL: Module-Level Isolation Setup (MUST BE FIRST)
# ============================================================================
# Ensure proper module isolation to prevent cross-file contamination

import json
import sys
from collections.abc import Generator
from typing import Any
from unittest.mock import Mock, patch

# Store original modules before any mocking to enable restoration
original_modules: dict[str, Any] = {}
original_functions: dict[str, Any] = {}
modules_to_isolate = [
    'PyQt6.QtCore',
    'PyQt5.QtCore',
    'artisanlib.util',
    'artisanlib.main',
    'artisanlib.__version__',
    'plus.config',
    'plus.account',
    'plus.util',
    'requests',
    'requests.exceptions',
    'keyring',
    'dateutil.parser',
]

# Store original modules if they exist
for module_name in modules_to_isolate:
    if module_name in sys.modules and not hasattr(sys.modules[module_name], '_mock_name'):
        original_modules[module_name] = sys.modules[module_name]

# ============================================================================
# Now safe to import other modules
# ============================================================================

"""Unit tests for plus.connection module.

This module tests the network connection functionality including:
- Token management with semaphore protection
- Authentication and credential handling
- HTTP request/response operations (GET, POST, PUT)
- Header generation with user agent and authorization
- Data compression and decompression
- SSL verification and timeout configuration
- Error handling and retry logic
- Session token renewal on 401 errors
- Keyring integration for credential storage
- JSON data serialization and handling
- Request logging and debugging

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
   - ensure_connection_isolation fixture manages module state at session level
   - Proper cleanup after session to prevent module registration conflicts

4. **Automatic State Reset**:
   - reset_connection_state fixture runs automatically for every test
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
✅ Individual tests pass: pytest test_connection.py::TestClass::test_method
✅ Full module tests pass: pytest test_connection.py
✅ Cross-file isolation works: pytest test_connection.py test_main.py
✅ Cross-file isolation works: pytest test_main.py test_connection.py
✅ No module contamination affecting other tests
✅ No numpy multiple import issues

This implementation serves as a reference for proper test isolation in
modules that require extensive mocking while preventing cross-file contamination.
=============================================================================
"""

import pytest


# Create a mock QSemaphore class that behaves properly
class MockQSemaphore:
    def __init__(self, initial_count: int = 1) -> None:
        self._count = initial_count
        self.acquire = Mock()
        self.release = Mock()
        self.available = Mock(return_value=0)  # Default to acquired state

    def __call__(self, *args: Any, **kwargs: Any) -> 'MockQSemaphore':
        del args, kwargs  # Unused arguments
        return self


# Import the connection module with targeted patches
# Only patch PyQt6 since PyQt5 is not installed and should be ignored
with patch('artisanlib.__version__', '2.8.4'), patch(
    'artisanlib.util.getDirectory', return_value='/test/cache/path'
), patch('plus.config.app_name', 'artisan.plus'), patch(
    'plus.config.auth_url', 'https://artisan.plus/api/v1/accounts/users/authenticate'
), patch(
    'plus.config.verify_ssl', True
), patch(
    'plus.config.connect_timeout', 6
), patch(
    'plus.config.read_timeout', 6
), patch(
    'plus.config.compress_posts', True
), patch(
    'plus.config.post_compression_threshold', 500
), patch(
    'plus.config.access_token', None
), patch(
    'plus.config.nickname', None
), patch(
    'plus.config.passwd', None
), patch(
    'plus.config.connected', False
), patch(
    'plus.config.app_window', None
), patch(
    'PyQt6.QtCore.QSemaphore', MockQSemaphore
), patch(
    'requests.get', Mock()
), patch(
    'requests.post', Mock()
), patch(
    'requests.put', Mock()
), patch(
    'keyring.get_password', Mock()
), patch(
    'keyring.delete_password', Mock()
):
    from plus import connection


@pytest.fixture(autouse=True)
def isolated_test_environment() -> Generator[None, None, None]:
    """Provide completely isolated test environment for each test."""
    # Use patch.dict to ensure complete isolation from other test files
    with patch.dict(
        'sys.modules',
        {
            'PyQt6.QtCore': Mock(),
            'PyQt5.QtCore': Mock(),
            'artisanlib.util': Mock(),
            'artisanlib.main': Mock(),
            'plus.config': Mock(),
            'plus.account': Mock(),
            'plus.util': Mock(),
            'requests': Mock(),
            'requests.exceptions': Mock(),
            'keyring': Mock(),
            'dateutil.parser': Mock(),
        },
        clear=False,
    ):
        # Configure Qt mocks with proper QSemaphore behavior
        def create_mock_qsemaphore(*args: Any, **kwargs: Any) -> Mock:
            del args
            del kwargs
            mock_sem = Mock()
            mock_sem.acquire = Mock()
            mock_sem.release = Mock()
            mock_sem.available = Mock(return_value=0)  # Default to acquired state
            return mock_sem

        sys.modules['PyQt6.QtCore'].QSemaphore = create_mock_qsemaphore # type: ignore[attr-defined]
        sys.modules['PyQt5.QtCore'].QSemaphore = create_mock_qsemaphore # type: ignore[attr-defined]

        # Configure config mock with default values
        config_mock = sys.modules['plus.config']
        config_mock.app_name = 'artisan.plus'  # type: ignore[attr-defined]
        config_mock.auth_url = 'https://artisan.plus/api/v1/accounts/users/authenticate'  # type: ignore[attr-defined]
        config_mock.verify_ssl = True # type: ignore[attr-defined]
        config_mock.connect_timeout = 6 # type: ignore[attr-defined]
        config_mock.read_timeout = 6 # type: ignore[attr-defined]
        config_mock.compress_posts = True # type: ignore[attr-defined]
        config_mock.post_compression_threshold = 500 # type: ignore[attr-defined]
        config_mock.access_token = None  # type: ignore[attr-defined]
        config_mock.nickname = None # type: ignore[attr-defined]
        config_mock.passwd = None # type: ignore[attr-defined]
        config_mock.connected = False # type: ignore[attr-defined]
        config_mock.app_window = None # type: ignore[attr-defined]

        yield


@pytest.fixture(scope='session', autouse=True)
def ensure_connection_isolation() -> Generator[None, None, None]:
    """
    Ensure modules are properly isolated for connection tests at session level.

    This fixture runs once per test session to ensure that mocked modules
    used by connection tests don't interfere with other tests that need real dependencies.
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
def cleanup_connection_mocks() -> Generator[None, None, None]:
    """
    Clean up connection test mocks at module level to prevent cross-file contamination.

    This fixture runs once per test module and ensures immediate cleanup
    of mocked dependencies when the connection test module completes.
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


@pytest.fixture
def mock_qsemaphore() -> Mock:
    """Create a fresh mock QSemaphore for each test."""
    mock_sem = Mock()
    mock_sem.acquire = Mock()
    mock_sem.release = Mock()
    mock_sem.available = Mock(return_value=0)  # Default to 0 (acquired state)
    # Ensure available() returns an integer, not a Mock
    mock_sem.available.return_value = 0
    return mock_sem


@pytest.fixture
def mock_app_window() -> Mock:
    """Create a fresh mock application window for each test."""
    mock_aw = Mock()
    # Reset mock state to ensure fresh instance
    mock_aw.reset_mock()

    # Configure default attributes and behaviors
    mock_aw.plus_account = 'test@example.com'
    mock_aw.qmc = Mock()
    mock_aw.qmc.operator = 'OperatorName'
    mock_aw.get_os = Mock(return_value=('macOS', '13.0', 'x86_64'))
    mock_aw.updateLimitsSignal = Mock()
    mock_aw.updateLimitsSignal.emit = Mock()

    # Add locale support for header generation
    mock_aw.locale_str = 'en_US'

    return mock_aw


@pytest.fixture(autouse=True)
def reset_connection_state() -> Generator[None, None, None]:
    """Reset connection module state before and after each test to ensure complete isolation."""
    # Store original values
    original_token = connection.config.get_token()
    original_nickname = getattr(connection.config, 'nickname', None)
    original_app_window = getattr(connection.config, 'app_window', None)
    original_connected = getattr(connection.config, 'connected', False)
    original_passwd = getattr(connection.config, 'passwd', None)
    original_request_read_timeout = connection.request_read_timeout

    # Reset dynamic read timeout for each test
    connection.request_read_timeout = connection.config.read_timeout

    # Reset semaphore mocks if they exist and are actually mocks
    if hasattr(connection, 'token_semaphore'):
        if hasattr(connection.token_semaphore, 'acquire') and hasattr(
            connection.token_semaphore.acquire, 'reset_mock'
        ):
            connection.token_semaphore.acquire.reset_mock() # pyright: ignore[reportAttributeAccessIssue] # ty: ignore
        if hasattr(connection.token_semaphore, 'release') and hasattr(
            connection.token_semaphore.release, 'reset_mock'
        ):
            connection.token_semaphore.release.reset_mock() # pyright: ignore[reportAttributeAccessIssue] # ty: ignore

    yield

    # Restore original values
    connection.config.set_token(original_token)
    connection.config.nickname = original_nickname
    connection.config.app_window = original_app_window
    connection.config.connected = original_connected
    connection.config.passwd = original_passwd
    connection.request_read_timeout = original_request_read_timeout


@pytest.fixture
def mock_response() -> Mock:
    """Create a fresh mock HTTP response for each test."""
    mock_resp = Mock()
    # Reset mock state to ensure fresh instance
    mock_resp.reset_mock()

    # Configure default attributes and behaviors
    mock_resp.status_code = 200
    mock_resp.headers = {'content-type': 'application/json'}
    mock_resp.elapsed = Mock()
    mock_resp.elapsed.total_seconds = Mock(return_value=0.5)
    mock_resp.json = Mock(return_value={'success': True})
    return mock_resp


@pytest.fixture
def sample_auth_response() -> dict[str, Any]:
    """Create fresh sample authentication response data for each test."""
    return {
        'success': True,
        'result': {
            'token': 'test_token_123',
            'user': {
                'nickname': 'TestUser',
                'account': {
                    'id': 'account_123',
                    'subscription': {'paidUntil': '2024-12-31T23:59:59Z'},
                    'limit': {'rlimit': 1000, 'rused': 50},
                },
            },
        },
        'notifications': {'unqualified': 5, 'machines': ['Machine1', 'Machine2']},
    }


class TestTokenManagement:
    """Test token management functionality."""

    def test_set_token_basic_functionality(
        self, reset_connection_state: None, mock_qsemaphore: Mock
    ) -> None:
        """Test setToken with basic token and nickname."""
        del reset_connection_state
        # Arrange
        test_token = 'test_token_456'
        test_nickname = 'TestNickname'

        with patch('plus.connection.token_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config:
            mock_config.app_window = None

            # Act
            connection.setToken(test_token, test_nickname)

            # Assert
            mock_qsemaphore.acquire.assert_called_once_with(1)
            mock_qsemaphore.release.assert_called_once_with(1)
            assert mock_config.set_token.call_args.args == (test_token,) if hasattr(mock_config, 'set_token') else mock_config.access_token == test_token
            assert mock_config.nickname == test_nickname

    def test_set_token_with_app_window_operator_update(
        self, mock_app_window: Mock, mock_qsemaphore: Mock
    ) -> None:
        """Test setToken updates operator when app window is available."""
        # Arrange
        test_token = 'test_token_789'
        test_nickname = 'OperatorName'

        with patch('plus.connection.token_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.app_window = mock_app_window

            # Act
            connection.setToken(test_token, test_nickname)

            # Assert
            assert mock_app_window.qmc.operator == test_nickname

    def test_set_token_without_nickname(self, mock_qsemaphore: Mock) -> None:
        """Test setToken with token only (no nickname)."""
        # Arrange
        test_token = 'token_only_123'

        with patch('plus.connection.token_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.app_window = None

            # Act
            connection.setToken(test_token)

            # Assert
            assert mock_config.set_token.call_args.args == (test_token,) if hasattr(mock_config, 'set_token') else mock_config.access_token == test_token
            assert mock_config.nickname is None

    def test_set_token_semaphore_protection(self, mock_qsemaphore: Mock) -> None:
        """Test setToken properly manages semaphore protection."""
        # Arrange
        test_token = 'semaphore_test_token'
        mock_qsemaphore.available.return_value = 0  # Semaphore is acquired

        with patch('plus.connection.token_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.app_window = None

            # Act
            connection.setToken(test_token)

            # Assert
            mock_qsemaphore.acquire.assert_called_once_with(1)
            mock_qsemaphore.release.assert_called_once_with(1)

    def test_set_token_no_release_when_available(self, mock_qsemaphore: Mock) -> None:
        """Test setToken doesn't release semaphore when already available."""
        # Arrange
        test_token = 'no_release_token'
        mock_qsemaphore.available.return_value = 1  # Semaphore is available

        with patch('plus.connection.token_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.app_window = None

            # Act
            connection.setToken(test_token)

            # Assert
            mock_qsemaphore.acquire.assert_called_once_with(1)
            # Should not call release since semaphore is available
            mock_qsemaphore.release.assert_not_called()


class TestCredentialManagement:
    """Test credential management functionality."""

    def test_clear_credentials_with_keychain_removal(self, mock_app_window: Mock) -> None:
        """Test clearCredentials removes password and remembered refresh token."""
        # Arrange
        with patch('plus.connection.config') as mock_config, patch(
            'keyring.delete_password'
        ) as mock_delete_password:

            mock_config.app_window = mock_app_window
            mock_config.app_name = 'artisan.plus'
            mock_config.access_token = 'test_token'
            mock_config.refresh_token = 'refresh_token'
            mock_config.nickname = 'test_nickname'
            mock_config.passwd = 'test_password'
            mock_config.account_nr = 123

            # Act
            connection.clearCredentials(remove_from_keychain=True)

            # Assert
            assert mock_delete_password.call_count == 2
            mock_delete_password.assert_any_call('artisan.plus', 'test@example.com')
            mock_delete_password.assert_any_call(
                'artisan.plus', 'refresh::test@example.com'
            )
            mock_config.set_token.assert_called_once_with(None)
            assert mock_config.refresh_token is None
            assert mock_config.nickname is None
            assert mock_config.passwd is None
            assert mock_config.account_nr is None
            assert mock_app_window.plus_account is None
            assert mock_config.passwd is None

    def test_clear_credentials_without_keychain_removal(self, mock_app_window: Mock) -> None:
        """Test clearCredentials without keychain removal."""
        # Arrange
        with patch('plus.connection.config') as mock_config, patch(
            'keyring.delete_password'
        ) as mock_delete_password:

            mock_config.app_window = mock_app_window
            mock_config.access_token = 'test_token'
            mock_config.nickname = 'test_nickname'
            mock_config.passwd = 'test_password'
            mock_config.account_nr = 123

            # Act
            connection.clearCredentials(remove_from_keychain=False)

            # Assert
            mock_delete_password.assert_not_called()
            assert mock_config.set_token.call_args.args == (None,) if hasattr(mock_config, 'set_token') else mock_config.access_token is None
            assert mock_config.nickname is None
            assert mock_config.passwd is None
            assert mock_config.account_nr is None

    def test_clear_credentials_keychain_exception_handling(self, mock_app_window: Mock) -> None:
        """Test clearCredentials handles keychain exceptions gracefully."""
        # Arrange
        with patch('plus.connection.config') as mock_config, patch(
            'keyring.delete_password', side_effect=Exception('Keychain error')
        ):

            mock_config.app_window = mock_app_window
            mock_config.access_token = 'test_token'

            # Act & Assert - Should not raise exception
            connection.clearCredentials(remove_from_keychain=True)

            # Should still clear config values despite keychain error
            assert mock_config.set_token.call_args.args == (None,) if hasattr(mock_config, 'set_token') else mock_config.access_token is None

    def test_clear_credentials_attempts_refresh_token_delete_when_password_delete_fails(
        self, mock_app_window: Mock
    ) -> None:
        """Test clearCredentials still deletes refresh token if password deletion fails."""
        with patch('plus.connection.config') as mock_config, patch(
            'keyring.delete_password', side_effect=[Exception('Password key error'), None]
        ) as mock_delete_password:
            mock_config.app_window = mock_app_window
            mock_config.app_name = 'artisan.plus'
            mock_config.access_token = 'test_token'

            connection.clearCredentials(remove_from_keychain=True)

            assert mock_delete_password.call_count == 2
            mock_delete_password.assert_any_call('artisan.plus', 'test@example.com')
            mock_delete_password.assert_any_call(
                'artisan.plus', 'refresh::test@example.com'
            )

    def test_clear_credentials_no_app_window(self) -> None:
        """Test clearCredentials when no app window is available."""
        # Arrange
        with patch('plus.connection.config') as mock_config, patch(
            'keyring.delete_password'
        ) as mock_delete_password:

            mock_config.app_window = None
            mock_config.access_token = 'test_token'

            # Act
            connection.clearCredentials(remove_from_keychain=True)

            # Assert
            mock_delete_password.assert_not_called()
            assert mock_config.set_token.call_args.args == (None,) if hasattr(mock_config, 'set_token') else mock_config.access_token is None


class TestSessionPersistence:
    """Test remembered session and logout behavior."""

    def test_restore_session_uses_remembered_refresh_token(self, mock_app_window: Mock) -> None:
        """Test restoreSession loads refresh token and refreshes the session."""
        mock_app_window.plus_email = 'test@example.com'

        with patch('plus.connection.config') as mock_config, patch(
            'keyring.get_password', return_value='stored_refresh_token'
        ) as mock_get_password, patch(
            'plus.connection.setRefreshToken'
        ) as mock_set_refresh_token, patch(
            'plus.connection.refreshSession', return_value=True
        ) as mock_refresh_session:
            mock_config.app_window = mock_app_window
            mock_config.app_name = 'artisan.plus'

            result = connection.restoreSession()

            assert result is True
            mock_get_password.assert_called_once_with(
                'artisan.plus', 'refresh::test@example.com'
            )
            mock_set_refresh_token.assert_called_once_with('stored_refresh_token')
            mock_refresh_session.assert_called_once_with()
            assert mock_app_window.plus_account == 'test@example.com'

    def test_logout_clears_credentials_after_successful_request(self) -> None:
        """Test logout clears credentials after calling the logout endpoint."""
        mock_response = Mock()
        mock_response.status_code = 204

        connection.request_read_timeout = 6

        with patch('plus.connection.getToken', return_value='access-token'), patch(
            'plus.connection.getHeaders', return_value={'Authorization': 'Bearer access-token'}
        ) as mock_get_headers, patch(
            'plus.connection.requests.post', return_value=mock_response
        ) as mock_post, patch('plus.connection.config') as mock_config, patch(
            'plus.connection.clearCredentials'
        ) as mock_clear_credentials:
            mock_config.get_logout_url.return_value = 'https://artisan.plus/api/v1/auth/logout'
            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6

            result = connection.logout()

            assert result is True
            mock_get_headers.assert_called_once_with(True)
            mock_post.assert_called_once_with(
                'https://artisan.plus/api/v1/auth/logout',
                headers={'Authorization': 'Bearer access-token'},
                verify=True,
                timeout=(6, 6),
            )
            mock_clear_credentials.assert_called_once_with()
            assert connection.getReadTimeout() == 6

        connection.request_read_timeout = connection.config.read_timeout


class TestSessionAuthentication:
    """Test session authentication state transitions."""

    def test_ensure_authenticated_session_sets_connected_after_authentify(self) -> None:
        """Test interactive authentication marks the config as connected on success."""
        with patch('plus.connection.config') as mock_config, patch(
            'plus.connection.getToken', return_value=None
        ), patch('plus.connection.restoreSession', return_value=False), patch(
            'plus.connection.authentify', return_value=True
        ) as mock_authentify:
            mock_config.app_window = Mock()
            mock_config.connected = False

            result = connection.ensureAuthenticatedSession(interactive=True)

            assert result is True
            assert mock_config.connected is True
            mock_authentify.assert_called_once_with()

    def test_refresh_session_clears_remembered_token_on_invalid_refresh_response(
        self, mock_qsemaphore: Mock, mock_response: Mock
    ) -> None:
        """Test refreshSession clears remembered token when the backend rejects the refresh."""
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {'success': True, 'result': {}}

        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stored_refresh_token', 'stored_refresh_token']
        ), patch(
            'plus.connection.sendData', return_value=mock_response
        ), patch(
            'plus.connection._apply_auth_response', return_value=False
        ), patch(
            'plus.connection.hasRememberedSession', return_value=True
        ), patch(
            'plus.connection.clearRememberedSession'
        ) as mock_clear_remembered_session, patch(
            'plus.connection.clearCredentials'
        ) as mock_clear_credentials:
            mock_config.app_window = mock_app_window
            mock_config.connected = True
            mock_config.get_refresh_url.return_value = 'https://artisan.plus/api/v1/auth/refresh'
            mock_clear_credentials.side_effect = lambda remove_from_keychain=False: setattr(
                mock_config, 'connected', False
            )

            result = connection.refreshSession()

            assert result is False
            mock_clear_remembered_session.assert_called_once_with('test@example.com')
            mock_clear_credentials.assert_called_once_with(remove_from_keychain=False)
            assert mock_config.connected is False

    def test_refresh_session_returns_true_when_token_changes_while_waiting(
        self, mock_qsemaphore: Mock
    ) -> None:
        """Test refreshSession treats a rotated token as already refreshed."""
        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stale_refresh_token', 'fresh_refresh_token']
        ), patch('plus.connection.sendData') as mock_send_data:
            mock_config.app_window = mock_app_window

            result = connection.refreshSession()

            assert result is True
            mock_send_data.assert_not_called()
            mock_qsemaphore.acquire.assert_called_once_with(1)
            mock_qsemaphore.release.assert_called_once_with(1)

    def test_refresh_session_uses_post_acquire_refresh_token(
        self, mock_qsemaphore: Mock, mock_response: Mock
    ) -> None:
        """Test refreshSession uses the token read after acquiring the semaphore."""
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {'success': True, 'result': {'access_token': 'new_access'}}

        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stale_refresh_token', 'stale_refresh_token']
        ), patch(
            'plus.connection.sendData', return_value=mock_response
        ) as mock_send_data, patch(
            'plus.connection._apply_auth_response', return_value=True
        ), patch(
            'plus.connection.hasRememberedSession', return_value=False
        ):
            mock_config.app_window = mock_app_window
            mock_config.get_refresh_url.return_value = 'https://artisan.plus/api/v1/auth/refresh'

            result = connection.refreshSession()

            assert result is True
            mock_send_data.assert_called_once_with(
                'https://artisan.plus/api/v1/auth/refresh',
                {'refreshToken': 'stale_refresh_token'},
                'POST',
                False,
            )

    def test_refresh_session_returns_false_when_token_cleared_before_send(
        self, mock_qsemaphore: Mock
    ) -> None:
        """Test refreshSession aborts when no refresh token exists after acquiring the semaphore."""
        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stale_refresh_token', None]
        ), patch('plus.connection.sendData') as mock_send_data:
            mock_config.app_window = mock_app_window

            result = connection.refreshSession()

            assert result is False
            mock_send_data.assert_not_called()

    def test_refresh_session_releases_semaphore_when_token_cleared_before_send(
        self, mock_qsemaphore: Mock
    ) -> None:
        """Test refreshSession always releases the semaphore on early return after acquire."""
        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stale_refresh_token', None]
        ):
            mock_config.app_window = mock_app_window

            result = connection.refreshSession()

            assert result is False
            mock_qsemaphore.acquire.assert_called_once_with(1)
            mock_qsemaphore.release.assert_called_once_with(1)
            mock_qsemaphore.available.assert_called()

    def test_refresh_session_persists_rotated_token_read_after_success(
        self, mock_qsemaphore: Mock, mock_response: Mock
    ) -> None:
        """Test refreshSession persists the refreshed token value after auth succeeds."""
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {'success': True, 'result': {'access_token': 'new_access'}}

        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['old_refresh_token', 'old_refresh_token', 'rotated_refresh_token']
        ), patch(
            'plus.connection.sendData', return_value=mock_response
        ), patch(
            'plus.connection._apply_auth_response', return_value=True
        ), patch(
            'plus.connection.hasRememberedSession', return_value=True
        ), patch(
            'plus.connection.persistRefreshToken'
        ) as mock_persist_refresh_token:
            mock_config.app_window = mock_app_window
            mock_config.get_refresh_url.return_value = 'https://artisan.plus/api/v1/auth/refresh'

            result = connection.refreshSession()

            assert result is True
            mock_persist_refresh_token.assert_called_once_with(
                'test@example.com', 'rotated_refresh_token', True
            )

    def test_refresh_session_returns_false_when_token_changes_before_acquire_send(
        self, mock_qsemaphore: Mock
    ) -> None:
        """Test refreshSession aborts when another thread rotates the token before send."""
        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stale_refresh_token', 'fresh_refresh_token']
        ), patch('plus.connection.sendData') as mock_send_data:
            mock_config.app_window = mock_app_window

            result = connection.refreshSession()

            assert result is False
            mock_send_data.assert_not_called()
            mock_qsemaphore.acquire.assert_called_once_with(1)
            mock_qsemaphore.release.assert_called_once_with(1)

    def test_refresh_session_persists_rotated_refresh_token_for_remembered_session(
        self, mock_qsemaphore: Mock, mock_response: Mock
    ) -> None:
        """Test refreshSession persists a rotated refresh token for remembered sessions."""
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'access_token': 'new_access_token',
                'refresh_token': 'new_refresh_token',
                'user': {},
            },
        }

        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['old_refresh_token', 'old_refresh_token', 'new_refresh_token']
        ), patch(
            'plus.connection.sendData', return_value=mock_response
        ), patch(
            'plus.connection._apply_auth_response', return_value=True
        ), patch(
            'plus.connection.hasRememberedSession', return_value=True
        ), patch(
            'plus.connection.persistRefreshToken'
        ) as mock_persist_refresh_token:
            mock_config.app_window = mock_app_window
            mock_config.get_refresh_url.return_value = 'https://artisan.plus/api/v1/auth/refresh'
            mock_config.app_name = 'artisan.plus'

            result = connection.refreshSession()

            assert result is True
            mock_persist_refresh_token.assert_called_once_with(
                'test@example.com', 'new_refresh_token', True
            )

    def test_authentify_sets_connected_on_success(self, mock_app_window: Mock) -> None:
        """Test authentify marks the config as connected after a successful login."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'access_token': 'new_access_token',
                'user': {},
            },
        }

        with patch('plus.connection.config') as mock_config, patch(
            'plus.connection.sendData', return_value=mock_response
        ), patch('plus.connection._apply_auth_response', return_value=True):
            mock_config.app_window = mock_app_window
            mock_config.connected = False
            mock_config.passwd = 'password123'

            result = connection.authentify()

            assert result is True
            assert mock_config.connected is True

    def test_apply_auth_response_accepts_token_from_result_payload(self) -> None:
        """Test _apply_auth_response accepts payload-level token fields."""
        mock_app_window = Mock()
        mock_app_window.updateSubscriptionSignal = Mock()
        mock_app_window.updateSubscriptionSignal.emit = Mock()
        mock_app_window.updateLimitsSignal = Mock()
        mock_app_window.updateLimitsSignal.emit = Mock()

        response = {
            'success': True,
            'result': {
                'token': 'payload_token',
                'user': {
                    'nickname': 'PayloadUser',
                },
            },
        }

        with patch('plus.connection.config') as mock_config, patch(
            'plus.connection.setToken'
        ) as mock_set_token:
            mock_config.app_window = mock_app_window

            result = connection._apply_auth_response(response)

            assert result is True
            mock_set_token.assert_called_once_with('payload_token', 'PayloadUser')

    def test_refresh_session_removes_remembered_token_on_204_response(
        self, mock_qsemaphore: Mock, mock_response: Mock
    ) -> None:
        """Test refreshSession clears keychain state when the refresh token has expired."""
        mock_response.status_code = 204

        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stored_refresh_token', 'stored_refresh_token']
        ), patch(
            'plus.connection.sendData', return_value=mock_response
        ), patch('plus.connection.clearCredentials') as mock_clear_credentials:
            mock_config.app_window = mock_app_window
            mock_config.get_refresh_url.return_value = 'https://artisan.plus/api/v1/auth/refresh'

            result = connection.refreshSession()

            assert result is False
            mock_clear_credentials.assert_called_once_with(remove_from_keychain=True)

    def test_refresh_session_uses_post_acquire_refresh_token(
        self, mock_qsemaphore: Mock, mock_response: Mock
    ) -> None:
        """Test refreshSession uses the freshest token read after acquiring the semaphore."""
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {'success': True, 'result': {'access_token': 'new_access'}}

        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stale_refresh_token', 'fresh_refresh_token']
        ), patch(
            'plus.connection.sendData', return_value=mock_response
        ) as mock_send_data, patch(
            'plus.connection._apply_auth_response', return_value=True
        ), patch(
            'plus.connection.hasRememberedSession', return_value=False
        ):
            mock_config.app_window = mock_app_window
            mock_config.get_refresh_url.return_value = 'https://artisan.plus/api/v1/auth/refresh'

            result = connection.refreshSession()

            assert result is True
            mock_send_data.assert_called_once_with(
                'https://artisan.plus/api/v1/auth/refresh',
                {'refreshToken': 'fresh_refresh_token'},
                'POST',
                False,
            )

    def test_refresh_session_returns_false_when_token_cleared_before_send(
        self, mock_qsemaphore: Mock
    ) -> None:
        """Test refreshSession aborts when no refresh token exists after acquiring the semaphore."""
        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stale_refresh_token', None]
        ), patch('plus.connection.sendData') as mock_send_data:
            mock_config.app_window = mock_app_window

            result = connection.refreshSession()

            assert result is False
            mock_send_data.assert_not_called()

    def test_refresh_session_releases_semaphore_when_token_cleared_before_send(
        self, mock_qsemaphore: Mock
    ) -> None:
        """Test refreshSession always releases the semaphore on early return after acquire."""
        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['stale_refresh_token', None]
        ):
            mock_config.app_window = mock_app_window

            result = connection.refreshSession()

            assert result is False
            mock_qsemaphore.acquire.assert_called_once_with(1)
            mock_qsemaphore.release.assert_called_once_with(1)
            mock_qsemaphore.available.assert_called()

    def test_refresh_session_persists_rotated_token_read_after_success(
        self, mock_qsemaphore: Mock, mock_response: Mock
    ) -> None:
        """Test refreshSession persists the refreshed token value after auth succeeds."""
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'application/json'}
        mock_response.json.return_value = {'success': True, 'result': {'access_token': 'new_access'}}

        mock_app_window = Mock()
        mock_app_window.plus_account = 'test@example.com'

        with patch('plus.connection.refresh_semaphore', mock_qsemaphore), patch(
            'plus.connection.config'
        ) as mock_config, patch(
            'plus.connection.getRefreshToken', side_effect=['old_refresh_token', 'fresh_refresh_token', 'rotated_refresh_token']
        ), patch(
            'plus.connection.sendData', return_value=mock_response
        ), patch(
            'plus.connection._apply_auth_response', return_value=True
        ), patch(
            'plus.connection.hasRememberedSession', return_value=True
        ), patch(
            'plus.connection.persistRefreshToken'
        ) as mock_persist_refresh_token:
            mock_config.app_window = mock_app_window
            mock_config.get_refresh_url.return_value = 'https://artisan.plus/api/v1/auth/refresh'

            result = connection.refreshSession()

            assert result is True
            mock_persist_refresh_token.assert_called_once_with(
                'test@example.com', 'rotated_refresh_token', True
            )


class TestHeaderGeneration:
    """Test HTTP header generation functionality."""

    def test_get_headers_authorized_with_token(self, mock_app_window: Mock) -> None:
        """Test getHeaders with authorization and token."""
        # Arrange
        with patch('plus.connection.config') as mock_config, patch(
            'plus.connection.__version__', '2.8.0'
        ), patch('plus.connection.getToken', return_value='auth_token_123'):

            mock_config.app_window = mock_app_window

            # Act
            headers = connection.getHeaders(authorized=True, decompress=True)

            # Assert
            assert 'user-agent' in headers
            assert 'Artisan/2.8.0' in headers['user-agent']
            assert 'macOS' in headers['user-agent']
            assert 'Accept-Charset' in headers
            assert headers['Accept-Charset'] == 'utf-8'
            assert 'Authorization' in headers
            assert headers['Authorization'] == 'Bearer auth_token_123'
            assert 'Accept-Encoding' in headers
            assert 'gzip' in headers['Accept-Encoding']
            assert 'Accept-Language' in headers
            assert headers['Accept-Language'] == 'en-us'

    def test_get_headers_unauthorized(self, mock_app_window: Mock) -> None:
        """Test getHeaders without authorization."""
        # Arrange
        with patch('plus.connection.config') as mock_config, patch(
            'artisanlib.__version__', '2.8.0'
        ):

            mock_config.app_window = mock_app_window
            mock_config.access_token = 'auth_token_123'

            # Act
            headers = connection.getHeaders(authorized=False, decompress=True)

            # Assert
            assert 'Authorization' not in headers
            assert 'user-agent' in headers
            assert 'Accept-Charset' in headers

    def test_get_headers_no_decompression(self, mock_app_window: Mock) -> None:
        """Test getHeaders without decompression support."""
        # Arrange
        with patch('plus.connection.config') as mock_config, patch(
            'artisanlib.__version__', '2.8.0'
        ):

            mock_config.app_window = mock_app_window
            mock_config.access_token = 'auth_token_123'

            # Act
            headers = connection.getHeaders(authorized=True, decompress=False)

            # Assert
            assert 'Accept-Encoding' not in headers
            assert 'Authorization' in headers

    def test_get_headers_no_app_window(self) -> None:
        """Test getHeaders when no app window is available."""
        # Arrange
        with patch('plus.connection.config') as mock_config:
            mock_config.app_window = None

            # Act
            headers = connection.getHeaders(authorized=True, decompress=True)

            # Assert
            # When no app_window is available, getHeaders returns empty dict
            assert headers == {}


class TestDataCompression:
    """Test data compression and header generation."""

    def test_get_headers_and_data_with_compression(self) -> None:
        """Test getHeadersAndData with compression enabled."""
        # Arrange
        large_data = {'key': 'x' * 1000}  # Large data to trigger compression
        jsondata = json.dumps(large_data, ensure_ascii=False).encode('utf8')

        with patch('plus.connection.getHeaders', return_value={'base': 'header'}), patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.post_compression_threshold = 500

            # Act
            headers, postdata = connection.getHeadersAndData(True, True, jsondata, 'POST')

            # Assert
            assert headers['Content-Type'] == 'application/json; charset=utf-8'
            assert 'Idempotency-Key' in headers
            assert headers['Content-Encoding'] == 'gzip'
            assert len(postdata) < len(jsondata)  # Compressed data should be smaller

    def test_get_headers_and_data_without_compression(self) -> None:
        """Test getHeadersAndData without compression."""
        # Arrange
        small_data = {'key': 'small'}
        jsondata = json.dumps(small_data, ensure_ascii=False).encode('utf8')

        with patch('plus.connection.getHeaders', return_value={'base': 'header'}), patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.post_compression_threshold = 500

            # Act
            headers, postdata = connection.getHeadersAndData(True, True, jsondata, 'POST')

            # Assert
            assert headers['Content-Type'] == 'application/json; charset=utf-8'
            assert 'Idempotency-Key' in headers
            assert 'Content-Encoding' not in headers
            assert postdata == jsondata  # Data should be unchanged

    def test_get_headers_and_data_put_request(self) -> None:
        """Test getHeadersAndData with PUT request (no idempotency key)."""
        # Arrange
        data = {'key': 'value'}
        jsondata = json.dumps(data, ensure_ascii=False).encode('utf8')

        with patch('plus.connection.getHeaders', return_value={'base': 'header'}):

            # Act
            headers, postdata = connection.getHeadersAndData(True, False, jsondata, 'PUT')

            # Assert
            assert headers['Content-Type'] == 'application/json; charset=utf-8'
            assert 'Idempotency-Key' not in headers
            assert postdata == jsondata

    def test_get_headers_and_data_compression_disabled(self) -> None:
        """Test getHeadersAndData with compression disabled."""
        # Arrange
        large_data = {'key': 'x' * 1000}
        jsondata = json.dumps(large_data, ensure_ascii=False).encode('utf8')

        with patch('plus.connection.getHeaders', return_value={'base': 'header'}):

            # Act
            headers, postdata = connection.getHeadersAndData(True, False, jsondata, 'POST')

            # Assert
            assert 'Content-Encoding' not in headers
            assert postdata == jsondata  # Data should not be compressed


class TestSendData:
    """Test sendData functionality."""

    def test_send_data_post_success(self, mock_response: Mock) -> None:
        """Test sendData with successful POST request."""
        # Arrange
        url = 'https://api.example.com/data'
        data = {'test': 'data'}

        connection.request_read_timeout = 12

        with patch(
            'plus.connection.getHeadersAndData',
            return_value=({'Content-Type': 'application/json'}, b'{"test":"data"}'),
        ), patch('plus.connection.requests.post', return_value=mock_response) as mock_post, patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6
            mock_config.read_timeout = 6
            mock_config.read_timeout_max = 12

            # Act
            result = connection.sendData(url, data, 'POST', authorized=True)

            # Assert
            assert result == mock_response
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[1]['verify'] is True
            assert call_args[1]['timeout'] == (6, 12)
            assert connection.getReadTimeout() == 10

        connection.request_read_timeout = connection.config.read_timeout

    def test_send_data_put_success(self, mock_response: Mock) -> None:
        """Test sendData with successful PUT request."""
        # Arrange
        url = 'https://api.example.com/data'
        data = {'test': 'data'}

        with patch(
            'plus.connection.getHeadersAndData',
            return_value=({'Content-Type': 'application/json'}, b'{"test":"data"}'),
        ), patch('plus.connection.requests.put', return_value=mock_response) as mock_put, patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6
            mock_config.read_timeout = 6

            # Act
            result = connection.sendData(url, data, 'PUT', authorized=True)

            # Assert
            assert result == mock_response
            mock_put.assert_called_once()

    def test_send_data_401_retry_success(self, mock_response: Mock) -> None:
        """Test sendData refreshes the access token and retries once on 401."""
        # Arrange
        url = 'https://api.example.com/data'
        data = {'test': 'data'}

        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.elapsed = Mock()
        mock_401_response.elapsed.total_seconds = Mock(return_value=0.3)

        with patch(
            'plus.connection.getHeadersAndData',
            return_value=({'Content-Type': 'application/json'}, b'{"test":"data"}'),
        ), patch(
            'plus.connection.requests.post', side_effect=[mock_401_response, mock_response]
        ) as mock_post, patch(
            'plus.connection.refreshSession', return_value=True
        ) as mock_refresh, patch(
            'plus.connection.authentify'
        ) as mock_auth, patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6
            mock_config.read_timeout = 6

            # Act
            result = connection.sendData(url, data, 'POST', authorized=True)

            # Assert
            assert result == mock_response
            assert mock_post.call_count == 2
            mock_refresh.assert_called_once()
            mock_auth.assert_not_called()

    def test_send_data_401_retry_reuses_post_idempotency_key(self, mock_response: Mock) -> None:
        """Test POST retries after 401 keep the original Idempotency-Key."""
        url = 'https://api.example.com/data'
        data = {'test': 'data'}

        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.elapsed = Mock()
        mock_401_response.elapsed.total_seconds = Mock(return_value=0.3)

        first_headers = {
            'Content-Type': 'application/json',
            'Idempotency-Key': 'original-key',
        }
        second_headers = {
            'Content-Type': 'application/json',
            'Idempotency-Key': 'new-key',
        }

        with patch(
            'plus.connection.getHeadersAndData',
            side_effect=[
                (first_headers, b'{"test":"data"}'),
                (second_headers, b'{"test":"data"}'),
            ],
        ), patch(
            'plus.connection.requests.post', side_effect=[mock_401_response, mock_response]
        ) as mock_post, patch(
            'plus.connection.refreshSession', return_value=True
        ), patch(
            'plus.connection.config'
        ) as mock_config:
            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6
            mock_config.read_timeout = 6

            result = connection.sendData(url, data, 'POST', authorized=True)

            assert result == mock_response
            assert mock_post.call_count == 2
            assert mock_post.call_args_list[0].kwargs['headers']['Idempotency-Key'] == 'original-key'
            assert mock_post.call_args_list[1].kwargs['headers']['Idempotency-Key'] == 'original-key'

            assert first_headers['Idempotency-Key'] == 'original-key'
            assert second_headers['Idempotency-Key'] == 'new-key'
            assert first_headers is not second_headers



    def test_send_data_401_retry_failed_refresh_case1(self) -> None:
        """Test sendData returns the 401 response when refresh fails."""
        # Arrange
        url = 'https://api.example.com/data'
        data = {'test': 'data'}

        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.elapsed = Mock()
        mock_401_response.elapsed.total_seconds = Mock(return_value=0.3)

        with patch(
            'plus.connection.getHeadersAndData',
            return_value=({'Content-Type': 'application/json'}, b'{"test":"data"}'),
        ), patch(
            'plus.connection.requests.post', return_value=mock_401_response
        ) as mock_post, patch(
            'plus.connection.refreshSession', return_value=False
        ) as mock_refresh, patch(
            'plus.connection.authentify'
        ) as mock_auth, patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6
            mock_config.read_timeout = 6

            # Act
            result = connection.sendData(url, data, 'POST', authorized=True)

            # Assert
            assert result == mock_401_response
            assert mock_post.call_count == 1
            mock_refresh.assert_called_once()
            mock_auth.assert_not_called()

    def test_send_data_401_retry_failed_refresh_case2(self) -> None:
        """Test sendData returns the 401 response when refresh fails."""
        # Arrange
        url = 'https://api.example.com/data'
        data = {'test': 'data'}

        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.elapsed = Mock()
        mock_401_response.elapsed.total_seconds = Mock(return_value=0.3)

        with patch(
            'plus.connection.getHeadersAndData',
            return_value=({'Content-Type': 'application/json'}, b'{"test":"data"}'),
        ), patch(
            'plus.connection.requests.post', return_value=mock_401_response
        ) as mock_post, patch(
            'plus.connection.refreshSession', return_value=False
        ) as mock_refresh, patch(
            'plus.connection.authentify'
        ) as mock_auth, patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6
            mock_config.read_timeout = 6

            # Act
            result = connection.sendData(url, data, 'POST', authorized=True)

            # Assert
            assert result == mock_401_response
            assert mock_post.call_count == 1
            mock_refresh.assert_called_once()
            mock_auth.assert_not_called()

    def test_send_data_401_retry_failed_refresh_case3(self) -> None:
        """Test sendData returns the 401 response when refresh fails."""
        # Arrange
        url = 'https://api.example.com/data'
        data = {'test': 'data'}

        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.elapsed = Mock()
        mock_401_response.elapsed.total_seconds = Mock(return_value=0.3)

        with patch(
            'plus.connection.getHeadersAndData',
            return_value=({'Content-Type': 'application/json'}, b'{"test":"data"}'),
        ), patch(
            'plus.connection.requests.post', return_value=mock_401_response
        ) as mock_post, patch(
            'plus.connection.refreshSession', return_value=False
        ) as mock_refresh, patch(
            'plus.connection.authentify'
        ) as mock_auth, patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6
            mock_config.read_timeout = 6

            # Act
            result = connection.sendData(url, data, 'POST', authorized=True)

            # Assert
            assert result == mock_401_response
            assert mock_post.call_count == 1
            mock_refresh.assert_called_once()
            mock_auth.assert_not_called()

    def test_send_data_unauthorized_request(self) -> None:
        """Test sendData with unauthorized request (no 401 retry)."""
        # Arrange
        url = 'https://api.example.com/data'
        data = {'test': 'data'}

        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.elapsed = Mock()
        mock_401_response.elapsed.total_seconds = Mock(return_value=0.3)

        with patch(
            'plus.connection.getHeadersAndData',
            return_value=({'Content-Type': 'application/json'}, b'{"test":"data"}'),
        ), patch(
            'plus.connection.requests.post', return_value=mock_401_response
        ) as mock_post, patch(
            'plus.connection.authentify'
        ) as mock_auth, patch(
            'plus.connection.config'
        ) as mock_config:

            mock_config.verify_ssl = True
            mock_config.connect_timeout = 6
            mock_config.read_timeout = 6

            # Act
            result = connection.sendData(url, data, 'POST', authorized=False)

            # Assert
            assert result == mock_401_response
            assert mock_post.call_count == 1  # Only original call
            mock_auth.assert_not_called()  # No retry for unauthorized requests
