# Installation

!!! quote "In one sentence"
    One command, zero configuration, Python 3.12+.

## Requirements

- **Python 3.12 or newer**
- That's it. The core only needs `aiosqlite` (installed automatically).

## Install the core

=== "uv"

    ```bash
    uv add zeitwerkzeug
    ```

=== "pip"

    ```bash
    pip install zeitwerkzeug
    ```

## Install with weather support

The `ClearWeather` condition talks to Open-Meteo and needs the `weather` extra:

=== "uv"

    ```bash
    uv add "zeitwerkzeug[weather]"
    ```

=== "pip"

    ```bash
    pip install "zeitwerkzeug[weather]"
    ```

!!! note "Windows users"
    On Windows, `tzdata` is installed automatically so timezone math always works.

## Verify it works

```python
from zeitwerkzeug import Location, SolarEvent

print(SolarEvent.SUNRISE)  # SolarEvent.SUNRISE
print(Location(52.52, 13.405, "Europe/Berlin"))
```

## Install from source (for contributors)

```bash
git clone https://github.com/bidyut18/zeitwerkzeug
cd zeitwerkzeug
uv sync          # installs dev tools: pytest, mypy, ruff
task test        # run the test suite
```

## Where to go next

-   [Quickstart →](quickstart.md) build your first solar-powered daemon in 5 minutes.
