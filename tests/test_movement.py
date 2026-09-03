from datetime import datetime, timedelta

import pytest

from app.movement.calculator import (
    MovementResult,
    calculate_movement,
)
from app.state.manager import PriceObservation


def make_observation(
    price: float,
    minutes: int,
) -> PriceObservation:
    return PriceObservation(
        timestamp=datetime(2026, 9, 2, 10, 0)
        + timedelta(minutes=minutes),
        price=price,
        volume=1_000,
    )


def test_no_movement_result_with_less_than_two_observations():
    observations = [
        make_observation(100.0, 0),
    ]

    result = calculate_movement(observations)

    assert result is None


def test_calculates_positive_movement():
    observations = [
        make_observation(100.0, 0),
        make_observation(110.0, 60),
    ]

    result = calculate_movement(observations)

    assert result is not None
    assert result.percentage_change == pytest.approx(10.0)


def test_calculates_negative_60_percent_movement():
    observations = [
        make_observation(100.0, 0),
        make_observation(40.0, 60),
    ]

    result = calculate_movement(observations)

    assert result is not None
    assert result.percentage_change == pytest.approx(-60.0)


def test_calculates_negative_80_percent_movement():
    observations = [
        make_observation(100.0, 0),
        make_observation(20.0, 60),
    ]

    result = calculate_movement(observations)

    assert result is not None
    assert result.percentage_change == pytest.approx(-80.0)


def test_calculates_positive_80_percent_movement():
    observations = [
        make_observation(100.0, 0),
        make_observation(180.0, 60),
    ]

    result = calculate_movement(observations)

    assert result is not None
    assert result.percentage_change == pytest.approx(80.0)


def test_calculates_duration():
    observations = [
        make_observation(100.0, 0),
        make_observation(120.0, 45),
    ]

    result = calculate_movement(observations)

    assert result is not None
    assert result.duration_seconds == pytest.approx(45 * 60)


def test_preserves_start_and_current_prices():
    observations = [
        make_observation(100.0, 0),
        make_observation(150.0, 30),
    ]

    result = calculate_movement(observations)

    assert result is not None
    assert result.start_price == 100.0
    assert result.current_price == 150.0


def test_handles_multiple_observations():
    observations = [
        make_observation(100.0, 0),
        make_observation(95.0, 10),
        make_observation(90.0, 20),
        make_observation(80.0, 30),
        make_observation(70.0, 40),
        make_observation(60.0, 50),
    ]

    result = calculate_movement(observations)

    assert result is not None
    assert result.percentage_change == pytest.approx(-40.0)


def test_rejects_zero_starting_price():
    observations = [
        make_observation(0.0, 0),
        make_observation(100.0, 60),
    ]

    with pytest.raises(ValueError):
        calculate_movement(observations)