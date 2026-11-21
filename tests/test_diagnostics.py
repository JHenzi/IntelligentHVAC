"""Diagnostics: daily rollup, fingerprint fit, unusual days, warm-up bias."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.intelligent_hvac import diagnostics as dg
from custom_components.intelligent_hvac.models import ThermalSegment

NY = ZoneInfo("America/New_York")


def _seg(kind: str, start: datetime, minutes: float, outdoor: float) -> ThermalSegment:
    return ThermalSegment(type=kind, start_time=start, end_time=start + timedelta(minutes=minutes),
                          duration_minutes=minutes, start_indoor_temp=68.0, end_indoor_temp=68.0,
                          avg_outdoor_temp=outdoor, total_temp_change=0.0, rate=0.0, is_valid=True)


def _day(d: date, heat_min: float, outdoor: float) -> list[ThermalSegment]:
    """A fully covered local day: one heating run at 06:00, idle otherwise."""
    start = datetime(d.year, d.month, d.day, tzinfo=NY)
    six = start + timedelta(hours=6)
    return [
        _seg("Passive_Decay", start, 360, outdoor),
        _seg("Active_Heating", six, heat_min, outdoor),
        _seg("Passive_Decay", six + timedelta(minutes=heat_min), 1080 - heat_min, outdoor),
    ]


def test_rollup_splits_at_local_midnight():
    # 23:00 -> 01:00 heating run: one hour on each day.
    start = datetime(2026, 11, 1, 23, tzinfo=NY)
    segs = [_seg("Passive_Decay", datetime(2026, 11, 1, tzinfo=NY), 23 * 60, 40.0),
            _seg("Active_Heating", start, 120, 40.0),
            _seg("Passive_Decay", start + timedelta(hours=2), 23 * 60, 40.0)]
    days = dg.daily_rollup(segs, NY, date(2026, 11, 5))
    assert [(d.day, d.heat_min) for d in days] == [("2026-11-01", 60.0), ("2026-11-02", 60.0)]
    assert days[0].hdd == pytest.approx(25.0)


def test_rollup_skips_today_and_poorly_covered_days():
    segs = _day(date(2026, 11, 1), 100, 40) + _day(date(2026, 11, 3), 100, 40)
    segs += [_seg("Active_Heating", datetime(2026, 11, 2, 6, tzinfo=NY), 60, 40)]  # 4% covered
    days = dg.daily_rollup(segs, NY, date(2026, 11, 3))
    assert [d.day for d in days] == ["2026-11-01"]


def test_rollup_handles_utc_segments_and_dicts():
    seg = _seg("Active_Heating", datetime(2026, 11, 2, 5, tzinfo=timezone.utc), 60, 30).to_dict()
    days = dg.daily_rollup([seg], NY, date(2026, 11, 3))
    assert days == []  # 1 h covered: below the coverage threshold, but no crash on dicts


def test_merge_keeps_better_covered_stored_day():
    stored = [{"day": "2026-11-01", "heat_min": 100, "mean_outdoor": 40, "hdd": 25, "coverage": 0.98}]
    fresh = [dg.DayStat("2026-11-01", 50, 40, 25, 0.86), dg.DayStat("2026-11-02", 70, 45, 20, 0.95)]
    merged = dg.merge_days(stored, fresh)
    assert [d["heat_min"] for d in merged] == [100, 70]


def _synthetic_days(slope=17.0, intercept=-80.0, n=40, outliers=()):
    rnd = random.Random(3)
    days = []
    for i in range(n):
        hdd = rnd.uniform(3, 30)
        heat = max(0.0, intercept + slope * hdd + rnd.gauss(0, 10))
        d = (date(2026, 10, 1) + timedelta(days=i)).isoformat()
        if i in outliers:
            heat += 200
        days.append({"day": d, "heat_min": heat, "mean_outdoor": 65 - hdd, "hdd": hdd, "coverage": 1.0})
    return days


def test_fit_recovers_slope_and_flags_outliers():
    days = _synthetic_days(outliers=(30,))
    s = dg.summarize(days, date(2026, 11, 15))
    assert s["fit"]["slope"] == pytest.approx(17.0, rel=0.1)
    assert s["fit"]["r2"] > 0.9
    flagged = [d["day"] for d in s["unusual_recent"]]
    assert flagged == ["2026-10-31"]
    assert s["unusual_recent"][0]["direction"] == "more_heat"


def test_fit_needs_enough_cold_days():
    assert dg.fit_fingerprint(_synthetic_days(n=5)) is None
    s = dg.summarize(_synthetic_days(n=5), date(2026, 11, 15))
    assert s["fit"] is None and s["unusual_recent"] == [] and s["points"]


def test_season_slopes_reveal_a_worse_house():
    a = _synthetic_days(slope=15.0)
    b = [{**d, "day": d["day"].replace("2026", "2027")} for d in _synthetic_days(slope=20.0)]
    s = dg.summarize(a + b, date(2027, 12, 1))
    assert s["seasons"]["2026-27"]["slope"] < s["seasons"]["2027-28"]["slope"]


def test_yesterday_flag():
    days = _synthetic_days(n=12)
    s = dg.summarize(days, date.fromisoformat(days[-1]["day"]) + timedelta(days=1))
    assert s["last_day_is_yesterday"] and s["last_day_scored"]["expected_min"] >= 0


def test_recovery_entry_ignores_resets():
    assert dg.recovery_entry({"factor": 1, "samples": 0}, datetime.now(timezone.utc)) is None
    row = dg.recovery_entry({"actual_min": 50, "predicted_min": 40, "outdoor_f": "31.2"},
                            datetime(2026, 11, 20, 6, tzinfo=timezone.utc))
    assert row["error_min"] == 10 and row["ratio"] == 1.25 and row["outdoor_f"] == 31.2


@pytest.mark.parametrize("errors,verdict", [
    ([1, -2, 3, 0, -1], "on_track"),
    ([12, 15, 9, 20, 11], "runs_late"),
    ([-12, -15, -9, -20, -11], "runs_early"),
    ([30, 30], "learning"),
])
def test_recovery_bias_verdicts(errors, verdict):
    log = [{"predicted_min": 40, "actual_min": 40 + e, "error_min": e, "ratio": (40 + e) / 40} for e in errors]
    assert dg.recovery_bias(log)["verdict"] == verdict


def _warm_days(n=20, intercept=58.0, slope=0.25):
    rnd = random.Random(5)
    out = []
    for i in range(n):
        high = rnd.uniform(40, 78)
        out.append({"day": (date(2026, 10, 15) + timedelta(days=i)).isoformat(), "heat_min": 0, "hdd": 0,
                    "mean_outdoor": 50, "coverage": 1, "cool_min": 0,
                    "indoor_peak": intercept + slope * high + rnd.gauss(0, 0.3), "out_high": high})
    return out


def test_warm_day_fit_and_forecast():
    days = _warm_days()
    highs = {d["day"]: d.pop("out_high") for d in days}
    today = date(2026, 11, 4)
    s = dg.summarize(days, today, highs, {
        "2026-11-04": {"high": 68, "condition": "sunny"},
        "2026-11-05": {"high": 50, "condition": "rainy"},
        "2026-11-03": {"high": 99, "condition": "sunny"},  # past: ignored
    })
    assert s["warm_fit"]["slope"] == pytest.approx(0.25, abs=0.03)
    assert s["warm_days"]["2026-11-04"]["indoor_peak"] == pytest.approx(75.0, abs=0.5)
    assert s["warm_days"]["2026-11-04"]["sunny"] is True
    assert s["warm_days"]["2026-11-05"]["sunny"] is False
    assert "2026-11-03" not in s["warm_days"]


def test_warm_day_fit_skips_ac_days_and_needs_data():
    days = _warm_days(n=20)
    for d in days[:15]:
        d["cool_min"] = 60
    assert dg.fit_warm_day(days, date(2026, 11, 10)) is None


def test_daily_highs_from_weather_and_sensor_records():
    recs = [
        {"timestamp": "2026-11-20T18:00:00+00:00", "state": "sunny", "attributes": {"temperature": 64}},
        {"timestamp": "2026-11-20T20:00:00+00:00", "state": "sunny", "attributes": {"temperature": 68}},
        {"last_updated": datetime(2026, 11, 21, 4, tzinfo=timezone.utc), "state": "71.5", "attributes": {}},
    ]
    # 04:00 UTC on the 21st is 23:00 on the 20th in New York.
    assert dg.daily_highs(recs, NY) == {"2026-11-20": 71.5}
    assert dg.merge_highs({"2026-11-20": 72.0}, {"2026-11-20": 71.5, "2026-11-21": 60}) == {"2026-11-20": 72.0, "2026-11-21": 60}


def test_rollup_records_indoor_peak_and_cooling():
    segs = _day(date(2026, 11, 1), 100, 40)
    segs[1].end_indoor_temp = 74.2
    days = dg.daily_rollup(segs, NY, date(2026, 11, 3))
    assert days[0].indoor_peak == 74.2 and days[0].cool_min == 0
