"""Built-in condition plugins, combinators, and custom condition factory."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import time as clock_time
from typing import Any, Protocol, TypeVar
from zoneinfo import ZoneInfo

from zeitwerkzeug.astro.location import Location
from zeitwerkzeug.astro.math_engine import sun_altitude
from zeitwerkzeug.interfaces import ExecutionContext

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Condition(Protocol):
    def evaluate(self, context: ExecutionContext) -> bool | Awaitable[bool]: ...


@dataclass(frozen=True, slots=True)
class AlwaysTrue:
    """Condition that always passes."""

    def evaluate(self, context: ExecutionContext) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class SunAltitudeAbove:
    """Require the sun to be above a minimum altitude."""

    location: Location
    min_altitude: float

    def evaluate(self, context: ExecutionContext) -> bool:
        return sun_altitude(context.triggered_at, self.location) >= self.min_altitude


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Require execution inside a local time window."""

    start: clock_time
    end: clock_time
    tz: str | None = None

    @property
    def tzinfo(self) -> datetime.tzinfo:
        if self.tz is None:
            return datetime.UTC
        return ZoneInfo(self.tz)

    def evaluate(self, context: ExecutionContext) -> bool:
        local_time = context.triggered_at.astimezone(self.tzinfo).time()

        if self.start <= self.end:
            return self.start <= local_time < self.end

        # Window crosses midnight.
        return local_time >= self.start or local_time < self.end


@dataclass(frozen=True, slots=True)
class All:
    """Logical AND combinator."""

    conditions: tuple[Condition, ...]

    def evaluate(self, context: ExecutionContext) -> bool:
        return all(condition.evaluate(context) for condition in self.conditions)


@dataclass(frozen=True, slots=True)
class Any:
    """Logical OR combinator."""

    conditions: tuple[Condition, ...]

    def evaluate(self, context: ExecutionContext) -> bool:
        return any(condition.evaluate(context) for condition in self.conditions)


@dataclass(frozen=True, slots=True)
class Not:
    """Logical NOT combinator."""

    condition: Condition

    def evaluate(self, context: ExecutionContext) -> bool:
        return not self.condition.evaluate(context)


@dataclass(slots=True)
class CachedCondition:
    """Wrap a condition to cache its result for a specified duration.
    
    This is useful for expensive operations like API calls (weather, etc.).
    The cache key is derived from the condition's hash and the evaluation context.
    """
    
    condition: Condition
    ttl_seconds: float = 300.0  # 5 minutes default
    _cache: dict[str, tuple[float, bool]] = field(default_factory=dict, init=False, repr=False, compare=False)
    
    def _make_cache_key(self, context: ExecutionContext) -> str:
        """Create a deterministic cache key from context and condition."""
        key_data = {
            "condition_type": type(self.condition).__name__,
            "job_name": context.job_name,
            "triggered_at": context.triggered_at.isoformat(),
        }
        key_string = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.sha256(key_string.encode()).hexdigest()[:16]
    
    def _is_expired(self, timestamp: float, now: datetime.datetime) -> bool:
        """Check if cached value has expired."""
        current_ts = now.timestamp()
        return (current_ts - timestamp) > self.ttl_seconds
    
    def evaluate(self, context: ExecutionContext) -> bool:
        """Evaluate condition with caching."""
        import asyncio
        
        # Check if condition returns async result
        result = self.condition.evaluate(context)
        if asyncio.iscoroutine(result):
            # For async conditions, we need async caching
            coro = self._evaluate_async(context)
            # Can't await here in sync method, so just evaluate directly
            # This shouldn't happen in normal usage as evaluate is called from async context
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        
        cache_key = self._make_cache_key(context)
        now = context.triggered_at
        
        # Try to get cached value
        if cache_key in self._cache:
            cached_ts, cached_value = self._cache[cache_key]
            if not self._is_expired(cached_ts, now):
                logger.debug(
                    "Cache hit for condition %s (job=%s)",
                    type(self.condition).__name__,
                    context.job_name,
                )
                return cached_value
        
        # Evaluate and cache
        value = bool(result)
        self._cache[cache_key] = (now.timestamp(), value)
        
        logger.debug(
            "Cache miss, evaluated condition %s (job=%s, result=%s)",
            type(self.condition).__name__,
            context.job_name,
            value,
        )
        
        return value
    
    async def _evaluate_async(self, context: ExecutionContext) -> bool:
        """Async version of evaluate for async conditions."""
        cache_key = self._make_cache_key(context)
        now = context.triggered_at
        
        # Try to get cached value
        if cache_key in self._cache:
            cached_ts, cached_value = self._cache[cache_key]
            if not self._is_expired(cached_ts, now):
                logger.debug(
                    "Cache hit for async condition %s (job=%s)",
                    type(self.condition).__name__,
                    context.job_name,
                )
                return cached_value
        
        # Evaluate and cache
        result = await self.condition.evaluate(context)
        value = bool(result)
        self._cache[cache_key] = (now.timestamp(), value)
        
        logger.debug(
            "Cache miss, evaluated async condition %s (job=%s, result=%s)",
            type(self.condition).__name__,
            context.job_name,
            value,
        )
        
        return value
    
    def clear_cache(self) -> None:
        """Clear the cache manually."""
        self._cache.clear()


@dataclass(frozen=True, slots=True)
class TimeoutCondition:
    """Wrap a condition to enforce a timeout during evaluation.
    
    Prevents hanging conditions from blocking job execution.
    Returns False if the condition times out.
    """
    
    condition: Condition
    timeout_seconds: float = 10.0
    on_timeout: bool = False  # Default to failing closed
    
    def evaluate(self, context: ExecutionContext) -> bool:
        """Evaluate condition with timeout."""
        import asyncio
        
        result = self.condition.evaluate(context)
        
        # Check if it's async
        if asyncio.iscoroutine(result):
            return self._evaluate_async_with_timeout(context)
        
        # Sync conditions don't need timeout (they should be fast)
        return bool(result)
    
    def _evaluate_async_with_timeout(self, context: ExecutionContext) -> bool:
        """Evaluate async condition with timeout."""
        import asyncio
        
        try:
            # Create event loop if needed
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop, create one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    asyncio.wait_for(
                        self.condition.evaluate(context),  # type: ignore
                        timeout=self.timeout_seconds,
                    )
                )
                loop.close()
                return bool(result)
            
            # If we're in a running loop, we can't block
            # Return on_timeout value and log warning
            logger.warning(
                "TimeoutCondition cannot apply timeout in async context. "
                "Use TimeoutCondition at job registration level instead. "
                "(job=%s, condition=%s)",
                context.job_name,
                type(self.condition).__name__,
            )
            return self.on_timeout
            
        except asyncio.TimeoutError:
            logger.warning(
                "Condition %s timed out after %s seconds (job=%s)",
                type(self.condition).__name__,
                self.timeout_seconds,
                context.job_name,
            )
            return self.on_timeout
        except Exception as exc:
            logger.exception(
                "Condition %s raised exception (job=%s): %s",
                type(self.condition).__name__,
                context.job_name,
                exc,
            )
            return self.on_timeout


# Type alias for condition factory functions
ConditionFactory = Callable[..., Condition]


class ConditionRegistry:
    """Registry for custom condition factories.
    
    Allows users to register and retrieve custom condition builders.
    Thread-safe for concurrent access.
    """
    
    _factories: dict[str, ConditionFactory] = {}
    
    @classmethod
    def register(cls, name: str) -> Callable[[ConditionFactory], ConditionFactory]:
        """Decorator to register a condition factory.
        
        Usage:
            @ConditionRegistry.register("my_condition")
            def my_condition_factory(param1: str, param2: int) -> Condition:
                return MyCustomCondition(param1, param2)
        """
        def decorator(factory: ConditionFactory) -> ConditionFactory:
            cls._factories[name] = factory
            logger.info("Registered condition factory: %s", name)
            return factory
        return decorator
    
    @classmethod
    def get_factory(cls, name: str) -> ConditionFactory | None:
        """Retrieve a registered condition factory by name."""
        return cls._factories.get(name)
    
    @classmethod
    def create(cls, name: str, **kwargs: Any) -> Condition | None:
        """Create a condition using a registered factory.
        
        Args:
            name: The registered factory name
            **kwargs: Arguments to pass to the factory
            
        Returns:
            A new Condition instance, or None if factory not found
        """
        factory = cls.get_factory(name)
        if factory is None:
            logger.warning("Condition factory not found: %s", name)
            return None
        try:
            return factory(**kwargs)
        except Exception as exc:
            logger.exception(
                "Failed to create condition '%s' with args %s: %s",
                name, kwargs, exc,
            )
            return None
    
    @classmethod
    def list_factories(cls) -> list[str]:
        """List all registered condition factory names."""
        return list(cls._factories.keys())
    
    @classmethod
    def clear(cls) -> None:
        """Clear all registered factories (useful for testing)."""
        cls._factories.clear()


def make_cached(condition: Condition, ttl_seconds: float = 300.0) -> CachedCondition:
    """Convenience function to wrap a condition with caching."""
    return CachedCondition(condition=condition, ttl_seconds=ttl_seconds)


def make_timeout(
    condition: Condition,
    timeout_seconds: float = 10.0,
    on_timeout: bool = False,
) -> TimeoutCondition:
    """Convenience function to wrap a condition with timeout."""
    return TimeoutCondition(
        condition=condition,
        timeout_seconds=timeout_seconds,
        on_timeout=on_timeout,
    )


ConditionLike = AlwaysTrue | SunAltitudeAbove | TimeWindow | All | Any | Not | CachedCondition | TimeoutCondition
