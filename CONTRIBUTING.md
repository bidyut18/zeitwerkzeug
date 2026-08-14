# Contributing to Zeitwerkzeug

Thank you for your interest in contributing to Zeitwerkzeug! This document provides guidelines and instructions to help you get started.

---

## Code of Conduct

We expect all contributors to adhere to the [Python Software Foundation Code of Conduct](https://www.python.org/psf/conduct/). Be respectful, inclusive, and constructive in all interactions.

---

## How Can I Contribute?

### Reporting Bugs

- Check if the issue already exists in the [issue tracker](https://github.com/bidyut18/zeitwerkzeug/issues).
- Provide a **minimal reproducible example** – include code, expected behaviour, and actual behaviour.
- Include your Python version, OS, and relevant package versions (`pip list`).

### Suggesting Enhancements

- Open a new issue with a clear description of the feature.
- Explain the use case and why it would be valuable.
- Be open to discussion – we may suggest alternatives or scope adjustments.

### Submitting Code (Pull Requests)

1. Fork the repository and create a new branch from `main`.
2. Make your changes, following the **Development Setup** below.
3. Write or update tests for your changes.
4. Ensure all checks pass (lint, format, type, tests).
5. Push your branch and open a pull request against `main`.
6. Provide a clear description of the changes and reference any related issues.

---

## Development Setup

### Prerequisites

- Python 3.11 or 3.12
- [uv](https://docs.astral.sh/uv/) – fast Python package installer and resolver
- [Task](https://taskfile.dev) – task runner for development commands

### 1. Clone the Repository

```bash
git clone https://github.com/bidyut18/zeitwerkzeug.git
cd zeitwerkzeug
```

### 2. Install Dependencies

```bash
task install
```

This runs `uv sync --all-extras --dev`, installing all dependencies (including optional weather extras and development tools).

### 3. Run the Full Check Locally

Before submitting a PR, always run:

```bash
task check
```

This will:
- Lint (`ruff check .`)
- Check formatting (`ruff format --check .`)
- Run type checking (`mypy src`)
- Run tests with coverage (`pytest`)

If you need to fix formatting or lint issues automatically:

```bash
task format
task lint:fix
```

---

## Code Style & Quality

We use the following tools to maintain code quality:

| Tool | Purpose | Configuration |
|------|---------|---------------|
| [Ruff](https://docs.astral.sh/ruff/) | Linting & formatting | `pyproject.toml` |
| [Mypy](https://mypy-lang.org/) | Static type checking | `pyproject.toml` (strict mode) |
| [Pytest](https://docs.pytest.org/) | Testing & coverage | `pyproject.toml` (75% coverage required) |

**Guidelines:**

- **Line length**: 100 characters (enforced by Ruff).
- **Type hints**: Required for all public functions and methods.
- **Docstrings**: Use Google-style docstrings for public APIs.
- **Imports**: Group standard library, third‑party, and local imports (Ruff will enforce).

---

## Testing

- All new features must be covered by tests.
- Run tests with: `task test` or `uv run pytest`.
- Coverage is configured to fail below 75% – ensure your changes don't lower coverage.
- Write **both** unit and integration tests where appropriate.

### Test Directory Structure

```
tests/
├── test_astro.py
├── test_context.py
├── test_daemon.py
├── test_integrations.py
├── test_personas.py
└── ...
```

### Running a Subset of Tests

```bash
uv run pytest tests/test_daemon.py -k "test_refresh"
```

---

## Commit Message Guidelines

- Use the **imperative** mood: "Fix bug" not "Fixed bug".
- Keep the subject line under 72 characters.
- Reference issue numbers in the body when applicable.

Example:

```
Add offset method to LazySchedule

Allows shifting a resolved schedule by a timedelta.
Closes #42.
```

---

## Pull Request Process

1. **Keep it focused** – one feature or fix per PR.
2. **Update documentation** if you change public APIs (README, docstrings, examples).
3. **Ensure CI passes** – the workflow runs on `main` and `dev` branches.
4. **Request a review** – a maintainer will review your PR within a few days.
5. **Be responsive** – address feedback promptly to keep the process smooth.

---

## Additional Resources
- [Issue Tracker](https://github.com/bidyut18/zeitwerkzeug/issues)
- [PyPI Package](https://pypi.org/project/zeitwerkzeug/)

---

## Questions?

If you need help, feel free to open a [discussion](https://github.com/bidyut18/zeitwerkzeug/discussions) or reach out via the issue tracker.

---

**Thank you for contributing to Zeitwerkzeug! 🌱**
