from datetime import datetime, timedelta

import pandas as pd
import pytest

from backend.conditions import historical_flags, rush_active
from backend.realtime_config import RAIN_MM
from pipeline.external.exposure_check import agreement_metrics
from scripts.build_condition_lift import build_lift


def test_rush_windows_are_weekday_local_half_open_intervals():
    assert rush_active(datetime(2022, 1, 3, 7, 0))
    assert rush_active(datetime(2022, 1, 3, 9, 59))
    assert not rush_active(datetime(2022, 1, 3, 10, 0))
    assert rush_active(datetime(2022, 1, 7, 18, 59))
    assert not rush_active(datetime(2022, 1, 8, 8, 0))


def test_shared_historical_flags_use_archive_threshold_and_is_day():
    flags = historical_flags(RAIN_MM, 0, datetime(2022, 1, 3, 7, 0))
    assert flags == {"rain": True, "night": True, "rush": True}
    assert historical_flags(RAIN_MM - 0.001, 1, datetime(2022, 1, 8, 8, 0)) == {
        "rain": False, "night": False, "rush": False,
    }


def test_builder_shrinks_cell_rate_keeps_safe_cell_unlifted_and_caps_multiplier():
    start = datetime(2018, 1, 1)
    hours = pd.date_range("2017-01-01", "2021-12-31 23:00:00", freq="h")
    first_2018_hour = 365 * 24
    weather = pd.DataFrame({
        "time": hours,
        "precipitation": [1.0 if first_2018_hour <= i < first_2018_hour + 5
                          or first_2018_hour + 10 <= i < first_2018_hour + 15
                          or (i >= first_2018_hour + 100 and (i - first_2018_hour - 100) % 10 == 0) else 0.0
                          for i in range(len(hours))],
        "is_day": 1,
    })
    crashes = pd.DataFrame([
        {"cell": "rain-prone", "Start_Time": start + timedelta(hours=i, minutes=15), "year": 2018}
        for i in range(10)
    ] + [
        {"cell": "safe", "Start_Time": start + timedelta(hours=i, minutes=15), "year": 2018}
        for i in range(10, 100)
    ])

    doc = build_lift(crashes, weather, through_year=2021)
    prone = doc["cells"]["rain-prone"]["conditions"]["rain"]
    assert doc["citywide"]["rain"]["p"] == 0.1
    assert prone["k"] == 5
    assert prone["rho"] == pytest.approx(3.0)
    assert prone["multiplier"] == 1.5  # uncapped value would be 2.0
    assert "safe" not in doc["cells"]  # its effective rho is below the prone threshold


def test_agreement_diagnostics_include_precision_recall_and_kappa():
    precision, recall, kappa = agreement_metrics([1, 1, 0, 0], [1, 0, 0, 0])
    assert precision == 1.0
    assert recall == 0.5
    assert kappa == 0.5
