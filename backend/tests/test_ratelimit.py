import fakeredis
import pytest

from app.errors import RateLimitedError
from app.ratelimit import RateBudget


def make_budget(limit: int = 3) -> RateBudget:
    return RateBudget(fakeredis.FakeRedis(), {"instagram": (limit, 900)})


def test_status_counts_recorded_requests_and_flags_near_limit() -> None:
    # Arrange
    budget = make_budget(limit=5)

    # Act
    budget.record("instagram", 4)
    status = budget.status("instagram")

    # Assert
    assert (status.used, status.limit, status.wait_seconds) == (4, 5, 0)
    assert status.near_limit is True


def test_ensure_available_refuses_when_budget_used_up() -> None:
    # Arrange
    budget = make_budget(limit=2)
    budget.record("instagram", 2)

    # Act / Assert
    with pytest.raises(RateLimitedError) as caught:
        budget.ensure_available("instagram")
    assert caught.value.retry_after is not None and caught.value.retry_after > 0
    assert "menit" in str(caught.value)


def test_cooldown_blocks_even_with_budget_left() -> None:
    # Arrange
    budget = make_budget(limit=100)

    # Act
    budget.cooldown("instagram", 120)

    # Assert
    with pytest.raises(RateLimitedError) as caught:
        budget.ensure_available("instagram")
    assert caught.value.retry_after == 120
    assert "2 menit" in str(caught.value)


def test_wait_for_share_is_zero_while_cost_fits_inside_share() -> None:
    # Arrange
    budget = make_budget(limit=100)
    budget.record("instagram", 50)

    # Act / Assert
    assert budget.wait_for_share("instagram", 8, 0.6) == 0


def test_wait_for_share_waits_until_old_hits_expire() -> None:
    # Arrange
    budget = make_budget(limit=100)
    budget.record("instagram", 55)

    # Act
    wait = budget.wait_for_share("instagram", 8, 0.6)

    # Assert
    assert 0 < wait <= 900
