"""Translate vague human phrases into concrete time blocks."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from zeitwerkzeug.exceptions import PersonaError
from zeitwerkzeug.personas.profile import PersonaProfile, TimeBlock

_WAKE_PLUS_RE = re.compile(
    r"wake\s*\+\s*(?P<value>\d+(?:\.\d+)?)\s*(?:h|hr|hour|hours)",
    re.IGNORECASE,
)

_SLEEP_MINUS_RE = re.compile(
    r"sleep\s*-\s*(?P<value>\d+(?:\.\d+)?)\s*(?:h|hr|hour|hours)",
    re.IGNORECASE,
)


class PersonaParser:
    """Parser bound to a persona profile."""

    def __init__(self, profile: PersonaProfile) -> None:
        self.profile = profile

    def parse(
        self,
        phrase: str,
        reference: datetime | None = None,
    ) -> TimeBlock:
        """Parse a phrase into a UTC TimeBlock."""
        if reference is None:
            reference = datetime.now(self.profile.tzinfo)

        reference = self.profile._localize(reference)
        block = self._parse_forward(phrase.strip().lower(), reference, depth=0)
        return block.to_utc()

    def _parse_forward(
        self,
        phrase: str,
        reference: datetime,
        depth: int,
    ) -> TimeBlock:
        block = self._parse(phrase, reference)

        if block.end <= reference and depth < 7:
            return self._parse_forward(
                phrase,
                reference + timedelta(days=1),
                depth + 1,
            )

        return block

    def _parse(self, phrase: str, reference: datetime) -> TimeBlock:
        awake = self.profile.awake_block(reference)
        wake = awake.start
        sleep = awake.end
        duration = awake.duration

        if "first thing" in phrase and "morning" in phrase:
            return TimeBlock(
                start=wake,
                end=wake + timedelta(hours=1, minutes=30),
                label="first_thing_in_the_morning",
            )

        if phrase == "morning" or "morning" in phrase and "late" not in phrase:
            return TimeBlock(
                start=wake + timedelta(hours=1),
                end=wake + timedelta(hours=4),
                label="morning",
            )

        if "late morning" in phrase:
            return TimeBlock(
                start=wake + timedelta(hours=4),
                end=wake + timedelta(hours=6),
                label="late_morning",
            )

        if "afternoon" in phrase and "late" not in phrase:
            return self.profile.proportional_block(
                reference,
                0.35,
                0.65,
                label="afternoon",
            )

        if "late afternoon" in phrase:
            return self.profile.proportional_block(
                reference,
                0.55,
                0.75,
                label="late_afternoon",
            )

        if "evening" in phrase:
            return TimeBlock(
                start=sleep - timedelta(hours=4),
                end=sleep - timedelta(hours=1),
                label="evening",
            )

        if "tonight" in phrase or "night" in phrase:
            return TimeBlock(
                start=sleep - timedelta(hours=3),
                end=sleep,
                label="tonight",
            )

        if "middle third" in phrase:
            return self.profile.proportional_block(
                reference,
                1.0 / 3.0,
                2.0 / 3.0,
                label="middle_third",
            )

        wake_match = _WAKE_PLUS_RE.search(phrase)
        if wake_match:
            hours = float(wake_match.group("value"))
            start = wake + timedelta(hours=hours)
            return TimeBlock(
                start=start,
                end=start + timedelta(hours=1),
                label="wake_plus",
            )

        sleep_match = _SLEEP_MINUS_RE.search(phrase)
        if sleep_match:
            hours = float(sleep_match.group("value"))
            start = sleep - timedelta(hours=hours)
            return TimeBlock(
                start=start,
                end=sleep,
                label="sleep_minus",
            )

        raise PersonaError(f"Unable to parse persona phrase: {phrase!r}")