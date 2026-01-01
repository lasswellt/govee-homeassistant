# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant Custom Component (HACS integration) for controlling Govee lights, LED strips, and smart devices via the Govee Cloud API v2.0. The integration includes its own built-in API client.

## Build and Test Commands

```bash
# Install test dependencies
pip install -r requirements_test.txt

# Run all tests with tox (includes flake8 linting + pytest)
tox

# Run pytest directly
pytest

# Run a single test file
pytest tests/test_config_flow.py

# Run a specific test
pytest tests/test_config_flow.py::test_form

# Format code with black
black .

# Check linting
flake8 .
```

## Architecture

### Component Structure (`custom_components/govee/`)

- **`__init__.py`**: Entry point for Home Assistant. Handles `async_setup_entry` to initialize the integration when configured via UI. Sets up the Govee API client and forwards platform setup to the light platform.

- **`light.py`**: Core platform implementation containing:
  - `GoveeDataUpdateCoordinator`: Manages polling state from Govee API, inherits from HA's `DataUpdateCoordinator`
  - `GoveeLightEntity`: Light entity implementation with support for on/off, brightness, RGB color, and color temperature

- **`config_flow.py`**: UI-based configuration flow. Contains `GoveeFlowHandler` for initial setup (API key validation) and `GoveeOptionsFlowHandler` for runtime options (poll interval, state disable options).

- **`learning_storage.py`**: `GoveeLearningStorage` class that persists device-specific learned parameters to `config/govee_learning.yaml`. Different Govee devices have varying brightness ranges (0-100 or 0-254) which are auto-learned.

- **`const.py`**: Domain constant (`govee`) and configuration keys

### API Client (`api/`)

- **`client.py`**: Async HTTP client for Govee Cloud API v2.0. Handles device listing, state queries, commands, and scene retrieval.
- **`const.py`**: API endpoints, capability types, and instance constants.

### State Management

The integration handles two state sources:
- **API state**: Polled from Govee cloud API
- **History state**: Assumed state after sending commands (before API confirmation)

Configuration option `DISABLE_ATTRIBUTE_UPDATES` allows disabling specific attributes from specific sources (e.g., `API:power_state`) as a workaround for API issues.

## Development Environment

A VS Code devcontainer is provided (`.devcontainer/`) that sets up a Home Assistant development instance accessible at `localhost:9123`.

## CI Workflows

- `tox.yaml`: Runs tests on Python 3.12 and 3.13
- `style.yml`: Runs black formatter check
- `hacs-hass.yaml`: HACS validation and hassfest checks
