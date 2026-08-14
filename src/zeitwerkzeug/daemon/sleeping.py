"""Sleep and wait utilities for the execution loop."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from zeitwerkzeug.daemon.constants import _MAX_WAIT_SECONDS

if TYPE_CHECKING:
    from zeitwerkzeug.daemon.clock import SystemClock


class SleepingMixin:
    """Provides _wait_until and _sleep_with_wake."""

    if TYPE_CHECKING:
        _running: bool
        clock: SystemClock
        _wake: asyncio.Event

    async def _wait_until(self, when: datetime) -> None:
        """Sleep until *when*, waking early if the loop is signalled."""
        while self._running:
            delta = (when - self.clock.now()).total_seconds()

            if delta <= 0:
                return

            self._wake.clear()

            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=min(delta, _MAX_WAIT_SECONDS),
                )

    async def _sleep_with_wake(self, interval: timedelta) -> None:
        """Sleep for *interval* unless the loop is signalled earlier."""
        if interval.total_seconds() <= 0:
            return

        self._wake.clear()

        with suppress(TimeoutError):
            await asyncio.wait_for(
                self._wake.wait(),
                timeout=interval.total_seconds(),
            )
