# Testing Guide

This document explains how to run tests, write new tests, and understand the testing infrastructure for the Govee integration.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Test Infrastructure](#test-infrastructure)
- [Running Tests](#running-tests)
- [Test Organization](#test-organization)
- [Writing Tests](#writing-tests)
- [Coverage Requirements](#coverage-requirements)
- [CI/CD Testing](#cicd-testing)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# Install test dependencies
pip install -r requirements_test.txt

# Run all tests
tox

# Run tests with coverage
pytest --cov=custom_components.govee --cov-report=term-missing

# Run specific test file
pytest tests/test_light.py

# Run specific test
pytest tests/test_light.py::TestGoveeLightEntity::test_turn_on_with_brightness
```

---

## Test Infrastructure

### Test Dependencies

**Required packages** (from `requirements_test.txt`):
- `pytest` - Testing framework
- `pytest-asyncio` - Async test support
- `pytest-cov` - Coverage measurement
- `pytest-homeassistant-custom-component` - HA test fixtures
- `flake8` - Linting
- `mypy` - Type checking
- `black` - Code formatting

### Configuration Files

#### `pytest.ini`
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts =
    --cov=custom_components.govee
    --cov-report=term-missing
    --cov-report=html
    --cov-report=xml
    --cov-fail-under=95
```

#### `.coveragerc`
```ini
[run]
source = custom_components/govee
omit =
    tests/*
    custom_components/govee/__init__.py

[report]
exclude_lines =
    pragma: no cover
    def __repr__
    raise AssertionError
    raise NotImplementedError
    if TYPE_CHECKING:
precision = 2
fail_under = 95
```

#### `tox.ini`
```ini
[tox]
envlist = py312,py313

[testenv]
deps = -r{toxinidir}/requirements_test.txt
commands =
    flake8 .
    mypy custom_components/govee
    pytest --cov=custom_components.govee --cov-report=term-missing --cov-report=html --cov-report=xml --cov-fail-under=95
```

---

## Running Tests

### Run All Tests

```bash
# Using tox (recommended - runs linting + type checking + tests)
tox

# Using pytest directly
pytest

# With verbose output
pytest -v

# With extra verbosity (show print statements)
pytest -vv -s
```

### Run Specific Tests

```bash
# Single test file
pytest tests/test_coordinator.py

# Single test class
pytest tests/test_light.py::TestGoveeLightEntity

# Single test method
pytest tests/test_light.py::TestGoveeLightEntity::test_turn_on

# Tests matching pattern
pytest -k "test_turn_on"

# Run only failed tests from last run
pytest --lf
```

### Coverage Reports

```bash
# Terminal report
pytest --cov=custom_components.govee --cov-report=term-missing

# HTML report (opens in browser)
pytest --cov=custom_components.govee --cov-report=html
open htmlcov/index.html

# XML report (for CI)
pytest --cov=custom_components.govee --cov-report=xml
```

### Type Checking

```bash
# Run mypy
mypy custom_components/govee

# With verbose output
mypy --verbose custom_components/govee
```

### Linting

```bash
# Run flake8
flake8 .

# Check specific file
flake8 custom_components/govee/light.py
```

---

## Test Organization

### Directory Structure

```
tests/
├── conftest.py                  # Shared fixtures
├── test_init.py                 # Integration setup tests
├── test_config_flow.py          # Config flow tests
├── test_coordinator.py          # Coordinator tests
├── test_entity.py               # Base entity tests
├── test_light.py                # Light platform tests
├── test_switch.py               # Switch platform tests
├── test_select.py               # Select platform tests
├── test_models.py               # Data model tests
├── test_services.py             # Service tests
└── api/
    ├── test_client.py           # API client tests
    └── test_exceptions.py       # Exception tests
```

### Test Count by File

| File | Tests | Coverage Focus |
|------|-------|----------------|
| `test_light.py` | 67 | Light entity, color modes, brightness conversion |
| `test_api/test_client.py` | 57 | API client, rate limiter, error handling |
| `test_models.py` | 50 | GoveeDevice, GoveeDeviceState, capability detection |
| `test_coordinator.py` | 42 | State updates, device discovery, scene caching |
| `test_exceptions.py` | 23 | Exception classes, error messages |
| `test_switch.py` | 20 | Socket switches, nightlight switches |
| `test_config_flow.py` | 19 | Setup flow, options flow, reauth flow |
| `test_select.py` | 17 | Scene selection, DIY scenes |
| `test_entity.py` | 15 | Base entity, availability, attributes |
| `test_init.py` | 14 | Integration setup, unload, reload |
| `test_services.py` | 11 | Segment color, music mode, scene refresh |

**Total**: 315+ tests, 95%+ coverage

---

## Writing Tests

### Test Structure

Use class-based organization:

```python
class TestComponentName:
    """Tests for ComponentName."""

    @pytest.mark.asyncio
    async def test_specific_behavior(self, hass, mock_api_client):
        """Test specific behavior."""
        # Arrange
        coordinator = GoveeDataUpdateCoordinator(...)

        # Act
        result = await coordinator.some_method()

        # Assert
        assert result == expected_value
```

### Using Fixtures

Fixtures are defined in `tests/conftest.py`:

```python
@pytest.mark.asyncio
async def test_with_fixtures(
    hass,                    # Home Assistant instance
    mock_api_client,         # Mocked API client
    mock_device_light,       # Factory for light devices
    mock_coordinator,        # Mocked coordinator
):
    """Test using shared fixtures."""
    device = mock_device_light()  # Create test device
    assert device.device_name == "Test Light"
```

### Factory Fixtures

Use factory fixtures for creating multiple test objects:

```python
@pytest.fixture
def mock_device_light():
    """Factory fixture for creating light devices."""
    def _create(
        device_id: str = "test_device",
        device_name: str = "Test Light",
        supports_rgb: bool = True,
    ):
        return GoveeDevice(
            device_id=device_id,
            device_name=device_name,
            # ... other properties
        )
    return _create

# Usage in tests
def test_multiple_devices(mock_device_light):
    device1 = mock_device_light(device_id="device_1")
    device2 = mock_device_light(device_id="device_2", supports_rgb=False)
```

### Mocking API Calls

Use `AsyncMock` for async methods:

```python
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_api_call(mock_api_client):
    """Test API call handling."""
    # Mock successful response
    mock_api_client.get_device_state = AsyncMock(
        return_value={"online": True, "powerState": "on"}
    )

    # Call method
    state = await mock_api_client.get_device_state("device_1", "H6XXX")

    # Verify call
    mock_api_client.get_device_state.assert_called_once_with("device_1", "H6XXX")
    assert state["online"] is True
```

### Testing Exceptions

```python
@pytest.mark.asyncio
async def test_error_handling(mock_api_client):
    """Test error handling."""
    # Mock API error
    mock_api_client.get_devices = AsyncMock(
        side_effect=GoveeAuthError("Invalid API key")
    )

    # Verify exception raised
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_setup()
```

### Testing State Updates

```python
@pytest.mark.asyncio
async def test_state_update(hass, mock_coordinator, mock_device_light):
    """Test state update."""
    device = mock_device_light()
    entity = GoveeLightEntity(mock_coordinator, device, entry)

    # Mock coordinator data
    mock_coordinator.data = {
        device.device_id: GoveeDeviceState(
            device_id=device.device_id,
            online=True,
            power_state=True,
            brightness=128,
        )
    }

    # Trigger state update
    await entity.async_update()

    # Verify state
    assert entity.is_on is True
    assert entity.brightness == 128
```

---

## Coverage Requirements

### Minimum Coverage

- **Overall**: 95%+
- **Per-File**: 90%+ (exceptions for simple files)
- **Critical Components**: 100% (coordinator, API client)

### Coverage Exclusions

Lines excluded from coverage:
```python
# pragma: no cover
def __repr__(self):  # String representations
    ...

if TYPE_CHECKING:  # Type-only imports
    ...

raise NotImplementedError  # Abstract methods
```

### Checking Coverage

```bash
# Generate coverage report
pytest --cov=custom_components.govee --cov-report=html

# Open HTML report
open htmlcov/index.html

# Find uncovered lines
pytest --cov=custom_components.govee --cov-report=term-missing
```

---

## CI/CD Testing

### GitHub Actions Workflow

Tests run automatically on:
- Push to `master` branch
- Pull requests
- Manual workflow dispatch

### Workflow File (`.github/workflows/tox.yaml`)

```yaml
name: Tests

on:
  push:
    branches: [master, develop]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.12", "3.13"]

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install dependencies
        run: pip install tox
      - name: Run tox
        run: tox
      - name: Upload coverage
        if: matrix.python-version == '3.12'
        uses: codecov/codecov-action@v4
        with:
          file: ./coverage.xml
```

### Pre-commit Hooks

Install pre-commit hooks:
```bash
pip install pre-commit
pre-commit install
```

Hooks run automatically before each commit:
- Black (formatting)
- Flake8 (linting)
- Mypy (type checking)

---

## Troubleshooting

### Common Issues

#### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'homeassistant'`

**Solution**:
```bash
pip install -r requirements_test.txt
```

#### Async Test Failures

**Problem**: `SyntaxError: 'await' outside async function`

**Solution**: Add `@pytest.mark.asyncio` decorator:
```python
@pytest.mark.asyncio
async def test_async_method():
    ...
```

#### Mock Not Working

**Problem**: `AttributeError: Mock object has no attribute 'return_value'`

**Solution**: Use `AsyncMock` for async methods:
```python
# Wrong
mock.method = MagicMock(return_value="value")

# Correct
mock.method = AsyncMock(return_value="value")
```

#### Coverage Not 100%

**Problem**: Can't reach 100% coverage on a file

**Solution**: Check for:
1. Unreachable code (dead branches)
2. Missing test cases (edge cases, error paths)
3. Lines that should be excluded (`pragma: no cover`)

### Debug Tips

```bash
# Show print statements
pytest -s

# Stop on first failure
pytest -x

# Show locals on failure
pytest -l

# Drop into debugger on failure
pytest --pdb

# Run with more verbosity
pytest -vv
```

---

## Best Practices

### DO

✅ Use descriptive test names: `test_turn_on_with_brightness_and_rgb`
✅ Test one thing per test
✅ Use fixtures for common setup
✅ Mock external dependencies (API calls)
✅ Test error cases
✅ Verify both success and failure paths
✅ Keep tests fast (mock slow operations)

### DON'T

❌ Test Home Assistant internals (trust the framework)
❌ Make real API calls in tests
❌ Share state between tests (use fresh fixtures)
❌ Skip writing tests for "simple" code
❌ Ignore test failures in CI
❌ Write tests that depend on execution order

---

## Example Test Patterns

### Testing Coordinator

```python
@pytest.mark.asyncio
async def test_coordinator_update(hass, mock_api_client):
    """Test coordinator state update."""
    # Setup
    coordinator = GoveeDataUpdateCoordinator(hass, entry, mock_api_client, timedelta(seconds=30))

    # Mock API response
    mock_api_client.get_device_state = AsyncMock(
        return_value={"online": True, "powerState": "on"}
    )

    # Execute
    await coordinator._async_update_data()

    # Verify
    assert coordinator.data["device_1"].online is True
    assert coordinator.data["device_1"].power_state is True
```

### Testing Light Entity

```python
@pytest.mark.asyncio
async def test_light_turn_on(hass, mock_coordinator, mock_device_light):
    """Test turning light on."""
    # Setup
    device = mock_device_light()
    entity = GoveeLightEntity(mock_coordinator, device, entry)

    # Execute
    await entity.async_turn_on(brightness=200, rgb_color=(255, 0, 0))

    # Verify control was called
    mock_coordinator.async_control_device.assert_called()
    call_args = mock_coordinator.async_control_device.call_args
    assert call_args[1]["value"] is True  # Power on
```

### Testing Error Handling

```python
@pytest.mark.asyncio
async def test_auth_error_triggers_reauth(mock_api_client):
    """Test auth error triggers re-authentication."""
    # Mock auth error
    mock_api_client.get_devices = AsyncMock(
        side_effect=GoveeAuthError("Invalid key")
    )

    # Verify ConfigEntryAuthFailed raised
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_setup()
```

---

## Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [Home Assistant Testing](https://developers.home-assistant.io/docs/development_testing)
- [Python Mocking](https://docs.python.org/3/library/unittest.mock.html)
