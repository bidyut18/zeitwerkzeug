# Zeitwerkzeug

Zeitwerkzeug (German for “time tool”) is an experiment in contextual time for Python. Rather than treating time as a fixed stream of timestamps, it models time as something derived from solar geometry, human rhythms, environmental conditions, and user intent.

<h3> ⏰<i>  Currently is Alpha phase . Not ready for production </i></h3>


## Why this project exists

Many automation and scheduling systems still assume a clock-based model of time. Zeitwerkzeug explores a more adaptive approach by making time objects aware of location, persona, and live conditions.

## Features

- Solar geometry support for dawn, golden hour, and dusk anchors
- Context-aware triggers for automation and IoT-style workflows
- Human persona abstractions for wake/sleep and daily rhythm modeling
- Async scheduling that can recalibrate over time
- Compatibility with standard Python datetime and timedelta workflows

## Installation

```bash
 pip install zeitwerkzeug
 ```
 or 
 ```bash 
 pip install zeitwerkzeug[weather]  
 ``` 
 for weather package


## Quick start

```python
from zeitwerkzeug.astro import SolarCalculator, Twilight
from zeitwerkzeug.personas import StandardWorker
from zeitwerkzeug.context import Trigger
from zeitwerkzeug.daemon import ZeitDaemon

calc = SolarCalculator(latitude=28.65, longitude=77.12)
sunset = calc.time_for_twilight(Twilight.CIVIL, date="2026-08-11")

persona = StandardWorker(wake_time="06:30", sleep_time="22:30")
trigger = Trigger(sunset, persona=persona)

daemon = ZeitDaemon()
daemon.register(trigger)
# await daemon.start()
```

## Why Zeitwerkzeug?

Time is often treated as a universal, absolute quantity. In practice, though, people experience time differently depending on location, routine, energy, and context. Zeitwerkzeug was built to explore that idea in software.

Instead of only scheduling by clock time, it allows you to think in terms of:

- sunrise, sunset, and other solar anchors
- human routines and persona-based rhythms
- environmental conditions that can influence when something should happen
- time objects that remain compatible with standard Python datetime tools

This makes it especially suitable for experiments in automation, scheduling, and context-aware systems.


## ☁️ Weather Integrations

Zeitwerkzeug includes optional weather-based triggers using the [Open-Meteo API](https://open-meteo.com/). 


**Licensing & Usage Limits:**
* **Non-Commercial / Open-Source:** Free to use without an API key. Please adhere to Open-Meteo's fair use policy (under 10,000 requests /day or 600 calls / min).
* **Commercial Use:** If you are using Zeitwerkzeug in a commercial, closed-source application, you **must** purchase an Open-Meteo commercial subscription and pass your API key to the integration:

```python
trigger = schedule.at(SolarEvent.GOLDEN_HOUR, location=loc).require(
    ClearWeather(lat=30.9, lon=75.8, max_cloud_cover=40, api_key="YOUR_COMMERCIAL_KEY")
)
```


**Attribution:** Weather data provided by [Open-Meteo.com](https://open-meteo.com/). Used under the CC BY 4.0 license.
## Development

1. Clone the repository
2. Create and activate a virtual environment
3. Install dependencies for local development
4. Run tests before submitting changes

## Contributing

Contributions are welcome. If you have ideas for new features, improvements, or bug fixes, please open an issue or submit a pull request.

### Contribution guidelines

- Follow the existing code style and keep changes focused
- Write clear commit messages and include tests when possible
- Open an issue first for larger changes or API discussions
- Be respectful of the project’s experimental scope and keep the public API stable where possible


## License

MIT License

## Development

Install [Task](https://taskfile.dev/installation/).

Then run:

```bash
task install
task lint
task typecheck
task test
```

## Roadmap

- Expand solar calculation coverage
- Add more personas and cultural profiles
- Improve deterministic testing and fixtures
- Stabilize the public API for broader adoption
