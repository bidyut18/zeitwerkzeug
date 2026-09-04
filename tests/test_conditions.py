"""Tests for condition caching, timeouts, and custom condition factory."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from zeitwerkzeug.context.base_hooks import (
    AlwaysTrue,
    CachedCondition,
    ConditionRegistry,
    TimeoutCondition,
    make_cached,
    make_timeout,
)
from zeitwerkzeug.interfaces import ExecutionContext

# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def sample_context() -> ExecutionContext:
    """Create a sample ExecutionContext for testing."""
    return ExecutionContext(
        job_name="test-job",
        scheduled_for=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        triggered_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        attempt=1,
        trigger=None,
        metadata={},
    )


@pytest.fixture
def mock_sync_condition() -> MagicMock:
    """Create a mock synchronous condition."""
    condition = MagicMock()
    condition.evaluate.return_value = True
    return condition


@pytest.fixture
def mock_async_condition() -> MagicMock:
    """Create a mock asynchronous condition."""
    condition = MagicMock()

    async def async_eval(*args, **kwargs):
        return True

    condition.evaluate.return_value = async_eval()
    return condition


# ==============================================================================
# CachedCondition Tests
# ==============================================================================


class TestCachedCondition:
    """Test suite for CachedCondition wrapper."""

    def test_cache_miss_then_hit(self, sample_context: ExecutionContext) -> None:
        """Test that cache miss is followed by cache hit."""
        call_count = 0

        class CountingCondition:
            def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                return True

        original = CountingCondition()
        cached = CachedCondition(condition=original, ttl_seconds=60.0)

        # First evaluation - cache miss
        result1 = cached.evaluate(sample_context)
        assert result1 is True
        assert call_count == 1

        # Second evaluation - cache hit (same context)
        result2 = cached.evaluate(sample_context)
        assert result2 is True
        assert call_count == 1  # Should not increment

    def test_cache_expires_after_ttl(self, sample_context: ExecutionContext) -> None:
        """Test that cache expires after TTL."""
        call_count = 0

        class CountingCondition:
            def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                return True

        original = CountingCondition()
        cached = CachedCondition(condition=original, ttl_seconds=0.1)  # 100ms TTL

        # First evaluation
        cached.evaluate(sample_context)
        assert call_count == 1

        # Wait for cache to expire
        time.sleep(0.15)

        # Create new context with slightly different time
        new_context = ExecutionContext(
            job_name="test-job",
            scheduled_for=sample_context.scheduled_for,
            triggered_at=datetime(2026, 1, 15, 12, 0, 1, tzinfo=UTC),  # 1 second later
            attempt=1,
            trigger=None,
            metadata={},
        )

        # Second evaluation - should be cache miss
        cached.evaluate(new_context)
        assert call_count == 2

    def test_clear_cache(self, sample_context: ExecutionContext) -> None:
        """Test manual cache clearing."""
        call_count = 0

        class CountingCondition:
            def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                return True

        original = CountingCondition()
        cached = CachedCondition(condition=original, ttl_seconds=60.0)

        # First evaluation
        cached.evaluate(sample_context)
        assert call_count == 1

        # Clear cache
        cached.clear_cache()

        # Second evaluation - should be cache miss
        cached.evaluate(sample_context)
        assert call_count == 2

    def test_different_jobs_have_separate_caches(self, sample_context: ExecutionContext) -> None:
        """Test that different jobs have separate cache entries."""
        call_count = 0

        class CountingCondition:
            def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                return True

        original = CountingCondition()
        cached = CachedCondition(condition=original, ttl_seconds=60.0)

        # Evaluate for job1
        context1 = sample_context
        cached.evaluate(context1)
        assert call_count == 1

        # Evaluate for job2 (different job name)
        context2 = ExecutionContext(
            job_name="test-job-2",
            scheduled_for=sample_context.scheduled_for,
            triggered_at=sample_context.triggered_at,
            attempt=1,
            trigger=None,
            metadata={},
        )
        cached.evaluate(context2)
        assert call_count == 2  # Different cache key

    def test_make_cached_helper(self, sample_context: ExecutionContext) -> None:
        """Test the make_cached convenience function."""
        call_count = 0

        class CountingCondition:
            def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                return True

        original = CountingCondition()
        cached = make_cached(original, ttl_seconds=60.0)

        assert isinstance(cached, CachedCondition)
        assert cached.ttl_seconds == 60.0

        # Verify caching works
        cached.evaluate(sample_context)
        cached.evaluate(sample_context)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_condition_caching(self, sample_context: ExecutionContext) -> None:
        """Test caching with async conditions."""
        call_count = 0

        class AsyncCountingCondition:
            async def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.001)  # Simulate async work
                return True

        original = AsyncCountingCondition()
        cached = CachedCondition(condition=original, ttl_seconds=60.0)

        # First evaluation
        result1 = await cached._evaluate_async(sample_context)
        assert result1 is True
        assert call_count == 1

        # Second evaluation - cache hit
        result2 = await cached._evaluate_async(sample_context)
        assert result2 is True
        assert call_count == 1  # Should not increment

    def test_cache_key_generation_consistency(self, sample_context: ExecutionContext) -> None:
        """Test that cache keys are generated consistently."""
        condition1 = AlwaysTrue()
        cached1 = CachedCondition(condition=condition1, ttl_seconds=60.0)

        condition2 = AlwaysTrue()
        cached2 = CachedCondition(condition=condition2, ttl_seconds=60.0)

        # Same context should produce same cache key
        key1 = cached1._make_cache_key(sample_context)
        key2 = cached2._make_cache_key(sample_context)

        # Keys should be the same for same condition type and context
        assert key1 == key2


# ==============================================================================
# TimeoutCondition Tests
# ==============================================================================


class TestTimeoutCondition:
    """Test suite for TimeoutCondition wrapper."""

    def test_sync_condition_no_timeout(self, sample_context: ExecutionContext) -> None:
        """Test that sync conditions pass through without timeout."""
        call_count = 0

        class CountingCondition:
            def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                return True

        original = CountingCondition()
        wrapped = TimeoutCondition(condition=original, timeout_seconds=0.1)

        result = wrapped.evaluate(sample_context)
        assert result is True
        assert call_count == 1

    def test_timeout_returns_on_timeout_value(self) -> None:
        """Test that timeout returns on_timeout value."""

        class SlowAsyncCondition:
            async def evaluate(self, context: ExecutionContext) -> bool:
                await asyncio.sleep(10)  # Very slow
                return True

        original = SlowAsyncCondition()
        wrapped = TimeoutCondition(condition=original, timeout_seconds=0.1, on_timeout=False)

        # Run in a new event loop to allow timeout
        loop = asyncio.new_event_loop()
        try:
            context = ExecutionContext(
                job_name="test-job",
                scheduled_for=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
                triggered_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
                attempt=1,
                trigger=None,
                metadata={},
            )
            result = wrapped._evaluate_async_with_timeout(context)
            assert result is False  # on_timeout value
        finally:
            loop.close()

    def test_timeout_with_on_timeout_true(self) -> None:
        """Test timeout with on_timeout=True."""

        class SlowAsyncCondition:
            async def evaluate(self, context: ExecutionContext) -> bool:
                await asyncio.sleep(10)
                return False

        original = SlowAsyncCondition()
        wrapped = TimeoutCondition(condition=original, timeout_seconds=0.1, on_timeout=True)

        loop = asyncio.new_event_loop()
        try:
            context = ExecutionContext(
                job_name="test-job",
                scheduled_for=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
                triggered_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
                attempt=1,
                trigger=None,
                metadata={},
            )
            result = wrapped._evaluate_async_with_timeout(context)
            assert result is True  # on_timeout value
        finally:
            loop.close()

    def test_fast_async_condition_completes(self, sample_context: ExecutionContext) -> None:
        """Test that fast async conditions complete normally."""
        call_count = 0

        class FastAsyncCondition:
            async def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.001)
                return True

        original = FastAsyncCondition()
        wrapped = TimeoutCondition(condition=original, timeout_seconds=1.0)

        loop = asyncio.new_event_loop()
        try:
            result = wrapped._evaluate_async_with_timeout(sample_context)
            assert result is True
            assert call_count == 1
        finally:
            loop.close()

    def test_make_timeout_helper(self, sample_context: ExecutionContext) -> None:
        """Test the make_timeout convenience function."""
        original = AlwaysTrue()
        wrapped = make_timeout(original, timeout_seconds=5.0, on_timeout=True)

        assert isinstance(wrapped, TimeoutCondition)
        assert wrapped.timeout_seconds == 5.0
        assert wrapped.on_timeout is True


# ==============================================================================
# ConditionRegistry Tests
# ==============================================================================


class TestConditionRegistry:
    """Test suite for ConditionRegistry."""

    def setup_method(self) -> None:
        """Clear registry before each test."""
        ConditionRegistry.clear()

    def teardown_method(self) -> None:
        """Clear registry after each test."""
        ConditionRegistry.clear()

    def test_register_and_get_factory(self) -> None:
        """Test registering and retrieving a factory."""

        def my_factory(param: str) -> AlwaysTrue:
            return AlwaysTrue()

        # Register using decorator
        ConditionRegistry.register("my_factory")(my_factory)

        # Retrieve
        retrieved = ConditionRegistry.get_factory("my_factory")
        assert retrieved is my_factory

    def test_register_decorator(self) -> None:
        """Test the register decorator."""

        @ConditionRegistry.register("decorated_factory")
        def factory_func(value: int) -> AlwaysTrue:
            return AlwaysTrue()

        assert "decorated_factory" in ConditionRegistry.list_factories()
        assert ConditionRegistry.get_factory("decorated_factory") is factory_func

    def test_create_condition(self) -> None:
        """Test creating a condition from factory."""

        def factory(value: int = 42) -> AlwaysTrue:
            return AlwaysTrue()

        ConditionRegistry.register("test_factory")(factory)

        condition = ConditionRegistry.create("test_factory", value=100)
        assert isinstance(condition, AlwaysTrue)

    def test_create_nonexistent_factory_returns_none(self) -> None:
        """Test that creating from nonexistent factory returns None."""
        result = ConditionRegistry.create("nonexistent")
        assert result is None

    def test_list_factories(self) -> None:
        """Test listing all registered factories."""
        ConditionRegistry.register("factory1")(lambda: AlwaysTrue())
        ConditionRegistry.register("factory2")(lambda: AlwaysTrue())
        ConditionRegistry.register("factory3")(lambda: AlwaysTrue())

        factories = ConditionRegistry.list_factories()
        assert len(factories) == 3
        assert "factory1" in factories
        assert "factory2" in factories
        assert "factory3" in factories

    def test_clear_factories(self) -> None:
        """Test clearing all factories."""
        ConditionRegistry.register("factory1")(lambda: AlwaysTrue())
        ConditionRegistry.register("factory2")(lambda: AlwaysTrue())

        assert len(ConditionRegistry.list_factories()) == 2

        ConditionRegistry.clear()

        assert len(ConditionRegistry.list_factories()) == 0

    def test_factory_exception_handling(self) -> None:
        """Test that factory exceptions are handled gracefully."""

        def bad_factory(**kwargs):
            raise ValueError("Factory error")

        ConditionRegistry.register("bad_factory")(bad_factory)

        # Should return None, not raise
        result = ConditionRegistry.create("bad_factory")
        assert result is None

    def test_custom_condition_example(self, sample_context: ExecutionContext) -> None:
        """Test a realistic custom condition registration example."""

        # Define a custom condition class
        class CustomThresholdCondition:
            def __init__(self, threshold: float, value: float):
                self.threshold = threshold
                self.value = value

            def evaluate(self, context: ExecutionContext) -> bool:
                return self.value > self.threshold

        # Register factory
        @ConditionRegistry.register("threshold")
        def threshold_factory(threshold: float, value: float) -> CustomThresholdCondition:
            return CustomThresholdCondition(threshold=threshold, value=value)

        # Create condition
        condition = ConditionRegistry.create("threshold", threshold=50.0, value=75.0)
        assert condition is not None
        assert condition.evaluate(sample_context) is True

        # Test with value below threshold
        condition2 = ConditionRegistry.create("threshold", threshold=50.0, value=25.0)
        assert condition2 is not None
        assert condition2.evaluate(sample_context) is False


# ==============================================================================
# Integration Tests
# ==============================================================================


class TestIntegration:
    """Integration tests combining multiple features."""

    def test_cached_timeout_condition(self) -> None:
        """Test combining caching and timeout wrappers."""
        call_count = 0

        class SlowSyncCondition:
            def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                time.sleep(0.01)  # Small delay
                return True

        original = SlowSyncCondition()

        # Wrap with both timeout and cache
        with_timeout = TimeoutCondition(condition=original, timeout_seconds=1.0)
        cached = CachedCondition(condition=with_timeout, ttl_seconds=60.0)

        context = ExecutionContext(
            job_name="test-job",
            scheduled_for=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            triggered_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            attempt=1,
            trigger=None,
            metadata={},
        )

        # First call
        result1 = cached.evaluate(context)
        assert result1 is True
        assert call_count == 1

        # Second call - should use cache
        result2 = cached.evaluate(context)
        assert result2 is True
        assert call_count == 1  # Still 1 due to cache

    def test_registry_with_cached_conditions(self, sample_context: ExecutionContext) -> None:
        """Test using cached conditions from registry."""
        ConditionRegistry.clear()

        call_count = 0

        class CountingCondition:
            def evaluate(self, context: ExecutionContext) -> bool:
                nonlocal call_count
                call_count += 1
                return True

        @ConditionRegistry.register("counting")
        def counting_factory() -> CountingCondition:
            return CountingCondition()

        # Create and wrap with cache
        condition = ConditionRegistry.create("counting")
        assert condition is not None

        cached = make_cached(condition, ttl_seconds=60.0)

        # Multiple evaluations
        cached.evaluate(sample_context)
        cached.evaluate(sample_context)
        cached.evaluate(sample_context)

        # Should only be called once due to caching
        assert call_count == 1

        ConditionRegistry.clear()
