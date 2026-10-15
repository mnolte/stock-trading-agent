
import pytest
from app.data.rate_limiter import RateLimitConfig, RateLimiter

pytestmark = pytest.mark.anyio


async def test_read_calls_under_limit() -> None:
    limiter = RateLimiter(RateLimitConfig(max_calls=150, window_seconds=60))
    async with limiter.read_call():
        pass
    async with limiter.read_call():
        pass
    assert len(limiter._calls) == 2


async def test_read_calls_blocked_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RateLimiter(RateLimitConfig(max_calls=2, window_seconds=60))

    now = [0.0]
    monkeypatch.setattr(
        "app.data.rate_limiter.time.monotonic", lambda: now[0]
    )
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    async with limiter.read_call():
        pass
    async with limiter.read_call():
        pass
    async with limiter.read_call():
        pass

    assert slept == [60.0]


async def test_run_read_retries_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RateLimiter(
        RateLimitConfig(max_calls=10, window_seconds=60, backoff_base_seconds=0.5)
    )
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    attempts = [0]

    def flaky() -> str:
        attempts[0] += 1
        if attempts[0] < 3:
            raise ConnectionError("timeout")
        return "ok"

    result = await limiter.run_read(flaky)
    assert result == "ok"
    assert attempts[0] == 3
    assert slept == [0.5, 1.0]


async def test_run_read_gives_up_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RateLimiter(
        RateLimitConfig(
            max_calls=10, window_seconds=60, backoff_base_seconds=0.01, max_retries=2
        )
    )

    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    def always_fails() -> None:
        raise TimeoutError("nope")

    with pytest.raises(TimeoutError):
        await limiter.run_read(always_fails)


async def test_run_read_calls_function(
) -> None:
    limiter = RateLimiter(RateLimitConfig(max_calls=10, window_seconds=60))
    result = await limiter.run_read(lambda x: x * 2, 21)
    assert result == 42
