# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## Github is used for everything

Github is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `master`.
2. If you've changed something, update the documentation.
3. Make sure your code lints (using black).
4. Test you contribution (using tox).
5. Issue that pull request!

### TODO Document: Work on library govee-api-laggat

* this library is added as subtree in '.git-subtree/python-govee-api'

```
# how we added it:
git subtree add --prefix .git-subtree/python-govee-api https://github.com/LaggAt/python-govee-api master

# you may want to pull latest master:
git subtree pull --prefix=.git-subtree/python-govee-api/ https://github.com/LaggAt/python-govee-api master

# if you made changes to the library don't forget to push the changes to bug/feature branches there before doing a pull request:
git subtree push --prefix=.git-subtree/python-govee-api/ https://github.com/LaggAt/python-govee-api feature/describe-your-feature

```


## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.

## Report bugs using Github's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose); it's that easy!

## Write bug reports with detail, background, and sample code

**Great Bug Reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

People *love* thorough bug reports. I'm not even kidding.

## Code Quality Requirements

This integration maintains **Platinum Quality Scale** certification. All contributions must meet these standards:

### 1. Type Annotations (Required)

- **100% type coverage** with mypy strict mode
- All functions, methods, and variables must have type hints
- Use `from __future__ import annotations` for forward references

```python
# Good
async def async_turn_on(self, brightness: int | None = None) -> None:
    """Turn on the light."""
    ...

# Bad - missing type hints
async def async_turn_on(self, brightness=None):
    ...
```

**Verify**:
```bash
mypy custom_components/govee
```

### 2. Test Coverage (Required)

- **Minimum 95% coverage** for all code
- **100% coverage** for critical components (coordinator, API client)
- Tests must pass in Python 3.12 and 3.13

**Test Requirements**:
- Use `pytest` with `pytest-asyncio`
- Mock all external dependencies (API calls, network)
- Test both success and error paths
- Include edge cases and boundary conditions

**Verify**:
```bash
pytest --cov=custom_components.govee --cov-fail-under=95
```

### 3. Code Style (Required)

- **Black** formatting (line length: 119)
- **Flake8** linting (no errors)
- Follow Home Assistant coding standards
- Comprehensive docstrings (Google style)

**Verify**:
```bash
black --check .
flake8 .
```

### 4. Documentation (Required)

- Update docstrings for modified code
- Add inline comments for complex logic
- Update README.md if behavior changes
- Document new features/services
- Update ARCHITECTURE.md for architectural changes

### 5. Async Architecture (Required)

- All I/O operations must be async
- Use `asyncio.gather()` for parallel operations
- Implement proper timeout handling
- Never block the event loop

```python
# Good - parallel execution
tasks = [fetch_device(d) for d in devices]
results = await asyncio.gather(*tasks)

# Bad - sequential execution
for device in devices:
    result = await fetch_device(device)
```

## Use a Consistent Coding Style

### Code Formatting

Use [black](https://github.com/ambv/black) to format code:

```bash
# Format all files
black .

# Check formatting
black --check .
```

### Linting

Use [flake8](https://flake8.pycqa.org/) for linting:

```bash
flake8 .
```

### Pre-commit Hooks (Recommended)

Install pre-commit hooks to automatically check code before commits:

```bash
pip install pre-commit
pre-commit install
```

Hooks run automatically on `git commit`:
- Black (formatting)
- Flake8 (linting)
- Mypy (type checking)

### Code Coverage with Codecov

Coverage reports are automatically uploaded to Codecov on every push to `master` or `develop`:

**Setup** (for maintainers):
1. Sign up at [codecov.io](https://codecov.io)
2. Add the repository to Codecov
3. GitHub Actions will automatically upload coverage reports

**Viewing Coverage**:
- View online: Coverage reports available at `https://codecov.io/gh/lasswellt/hacs-govee`
- View locally: Run `pytest --cov-report=html` and open `htmlcov/index.html`

**Coverage Requirements**:
- Minimum overall coverage: **95%**
- Critical components (coordinator, API client): **100%**
- Pull requests must not decrease coverage

## Test Your Code Modification

### Run Full Test Suite

```bash
# Run all tests with tox (recommended)
tox

# Run specific test file
pytest tests/test_light.py

# Run with coverage report
pytest --cov=custom_components.govee --cov-report=html
```

### Development Environment

This custom component is based on [integration_blueprint template](https://github.com/custom-components/integration_blueprint).

**VS Code DevContainer**:
It comes with a development environment in a container, easy to launch
if you use Visual Studio Code. With this container you will have a stand alone
Home Assistant instance running and already configured with the included
[`.devcontainer/configuration.yaml`](https://github.com/oncleben31/ha-pool_pump/blob/master/.devcontainer/configuration.yaml)
file.

**Local Development**:
1. Install Home Assistant in a virtual environment
2. Symlink integration to HA config: `ln -s $(pwd)/custom_components/govee ~/.homeassistant/custom_components/`
3. Restart Home Assistant
4. Add integration via UI

## Pull Request Checklist

Before submitting a PR, ensure:

- [ ] Code passes `tox` (linting, type checking, tests)
- [ ] Test coverage is ≥95%
- [ ] All tests pass in Python 3.12 and 3.13
- [ ] Code is formatted with Black
- [ ] Type hints are complete (mypy passes)
- [ ] Docstrings are updated
- [ ] README.md is updated if needed
- [ ] ARCHITECTURE.md is updated if needed
- [ ] No breaking changes (or documented if necessary)
- [ ] Commit messages follow convention:
  ```
  feat(component): add new feature
  fix(coordinator): resolve state sync issue
  docs(readme): update installation guide
  ```

## Code Review Process

1. **Automated Checks**: GitHub Actions runs tests, linting, type checking
2. **Manual Review**: Maintainer reviews code quality, architecture, tests
3. **Feedback**: Address any requested changes
4. **Approval**: Once approved, maintainer will merge

**Review Criteria**:
- Code quality and maintainability
- Test coverage and quality
- Performance implications
- Breaking change justification
- Documentation completeness

## License

By contributing, you agree that your contributions will be licensed under its MIT License.
