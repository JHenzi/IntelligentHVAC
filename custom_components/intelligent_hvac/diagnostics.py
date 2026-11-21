"""House diagnostics: heating runtime vs cold, unusual days, warm-up accuracy.

Pure functions (no Home Assistant imports) so they can be tested and run
offline against a stored segment file.

* Daily rollup: per local day, minutes of heating, mean outdoor temperature and
  heating degree days (HDD, base 65°F). Kept in its own store so it outlives the
  segment retention window: this is the multi-year record.
* Fingerprint: least-squares line ``heat_min = intercept + slope * HDD``. The
  slope (minutes of heating per °F-day) is the house's fingerprint; if it
  creeps up from season to season, the house or the furnace got worse.
* Unusual days: days far off the line (robust sigma from the residuals' MAD).
* Warm-up log: each measured morning heat-up, predicted vs actual, and the
  bias over recent runs.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Iterable

HDD_BASE_F = 65.0
MIN_DAY_COVERAGE = 0.85  # fraction of the day covered by stored segments
MIN_FIT_HDD = 3.0  # days milder than this say little about the slope
MIN_FIT_DAYS = 10
ROLLING_FIT_DAYS = 60
UNUSUAL_SIGMA = 3.0
UNUSUAL_MIN_RESIDUAL_MIN = 30.0
RECENT_UNUSUAL_DAYS = 30

RECOVERY_EVENT = "ihvac_recovery_measured"
BIAS_WINDOW_RUNS = 14
MIN_BIAS_RUNS = 5
MAX_RECOVERY_LOG = 400


@dataclass
class DayStat:
    """One local day."""

    day: str  # ISO date
    heat_min: float
    mean_outdoor: float
    hdd: float
    coverage: float
    cool_min: float = 0.0
    indoor_peak: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get(seg: Any, name: str) -> Any:
    return seg[name] if isinstance(seg, dict) else getattr(seg, name)


def _as_dt(value: Any) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def daily_rollup(segments: Iterable[Any], tz: tzinfo, today: date) -> list[DayStat]:
    """Split segments at local midnight and total each complete past day.

    Days before ``today`` with at least MIN_DAY_COVERAGE of the day covered by
    segments are returned. Heating runs are always stored, so heating minutes
    are complete; the outdoor mean is duration-weighted over the covered part.
    """
    acc: dict[date, list[float]] = {}  # day -> [covered, heat, outdoor_weighted, outdoor_minutes, cool]
    peaks: dict[date, float] = {}
    for seg in segments:
        start, end = _as_dt(_get(seg, "start_time")), _as_dt(_get(seg, "end_time"))
        outdoor = _get(seg, "avg_outdoor_temp")
        heating = _get(seg, "type") == "Active_Heating"
        cooling = _get(seg, "type") == "Active_Cooling"
        for when, temp in ((start, _get(seg, "start_indoor_temp")), (end, _get(seg, "end_indoor_temp"))):
            if temp is not None:
                d = when.astimezone(tz).date()
                peaks[d] = max(peaks.get(d, temp), temp)
        t = start
        while t < end:
            local_day = t.astimezone(tz).date()
            midnight = datetime.combine(local_day + timedelta(days=1), time(), tz)
            nxt = min(end, midnight)
            minutes = (nxt - t).total_seconds() / 60
            a = acc.setdefault(local_day, [0.0, 0.0, 0.0, 0.0, 0.0])
            a[0] += minutes
            if heating:
                a[1] += minutes
            if cooling:
                a[4] += minutes
            if outdoor is not None:
                a[2] += minutes * outdoor
                a[3] += minutes
            t = nxt

    days = []
    for d in sorted(acc):
        covered, heat, ow, om, cool = acc[d]
        if d >= today or om <= 0:
            continue
        coverage = min(covered / 1440, 1.0)
        if coverage < MIN_DAY_COVERAGE:
            continue
        mean_out = ow / om
        days.append(DayStat(
            day=d.isoformat(),
            heat_min=round(heat, 1),
            mean_outdoor=round(mean_out, 2),
            hdd=round(max(0.0, HDD_BASE_F - mean_out), 2),
            coverage=round(coverage, 3),
            cool_min=round(cool, 1),
            indoor_peak=None if d not in peaks else round(peaks[d], 1),
        ))
    return days


def merge_days(stored: list[dict[str, Any]], fresh: list[DayStat]) -> list[dict[str, Any]]:
    """Merge a fresh rollup into the stored record.

    A fresh day replaces the stored one unless it covers less of the day (its
    segments may have been pruned since).
    """
    by_day = {d["day"]: d for d in stored}
    for d in fresh:
        old = by_day.get(d.day)
        if old is None or d.coverage >= old.get("coverage", 0) - 1e-9:
            by_day[d.day] = {**(old or {}), **d.to_dict()}
    return [by_day[k] for k in sorted(by_day)]


def fit_fingerprint(days: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Least-squares heat_min = intercept + slope * hdd over days with HDD ≥ 3."""
    pts = [(d["hdd"], d["heat_min"]) for d in days if d["hdd"] >= MIN_FIT_HDD]
    n = len(pts)
    if n < MIN_FIT_DAYS:
        return None
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx <= 1e-9:
        return None
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in pts]
    ss_tot = sum((y - my) ** 2 for _, y in pts)
    r2 = 1 - sum(r * r for r in resid) / ss_tot if ss_tot > 0 else 0.0
    med = statistics.median(resid)
    sigma = 1.4826 * statistics.median(abs(r - med) for r in resid)
    if sigma <= 0:
        sigma = math.sqrt(sum(r * r for r in resid) / max(n - 2, 1))
    return {
        "slope": round(slope, 2),
        "intercept": round(intercept, 1),
        "r2": round(r2, 3),
        "days": n,
        "sigma": round(sigma, 1),
        "first_day": min(d["day"] for d in days if d["hdd"] >= MIN_FIT_HDD),
        "last_day": max(d["day"] for d in days if d["hdd"] >= MIN_FIT_HDD),
    }


def expected_minutes(fit: dict[str, Any], hdd: float) -> float:
    return max(0.0, fit["intercept"] + fit["slope"] * hdd)


def score_day(day: dict[str, Any], fit: dict[str, Any]) -> dict[str, Any]:
    exp = expected_minutes(fit, day["hdd"])
    resid = day["heat_min"] - exp
    unusual = (
        day["hdd"] >= MIN_FIT_HDD
        and abs(resid) > max(UNUSUAL_SIGMA * fit["sigma"], UNUSUAL_MIN_RESIDUAL_MIN)
    )
    return {
        **day,
        "expected_min": round(exp, 1),
        "residual_min": round(resid, 1),
        "unusual": unusual,
        "direction": None if not unusual else ("more_heat" if resid > 0 else "less_heat"),
    }


def season_of(day_iso: str) -> str:
    """Heating season label, July–June: 2026-11-03 -> '2026-27'."""
    d = date.fromisoformat(day_iso)
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y}-{str(y + 1)[-2:]}"


def summarize(
    days: list[dict[str, Any]],
    today: date,
    highs: dict[str, float] | None = None,
    forecast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the sensors need from the daily record.

    ``highs`` maps ISO day -> measured outdoor high; ``forecast`` maps ISO day ->
    forecast high (today and the coming days).
    """
    if highs:
        days = [{**d, "out_high": highs[d["day"]]} if d["day"] in highs else d for d in days]
    fit = fit_fingerprint(days)
    cutoff = (today - timedelta(days=ROLLING_FIT_DAYS)).isoformat()
    rolling = fit_fingerprint([d for d in days if d["day"] >= cutoff])

    seasons: dict[str, Any] = {}
    for label in sorted({season_of(d["day"]) for d in days}):
        f = fit_fingerprint([d for d in days if season_of(d["day"]) == label])
        if f:
            seasons[label] = {k: f[k] for k in ("slope", "intercept", "r2", "days")}

    scored = [score_day(d, fit) for d in days] if fit else []
    recent_cut = (today - timedelta(days=RECENT_UNUSUAL_DAYS)).isoformat()
    unusual = [d for d in scored if d["unusual"] and d["day"] >= recent_cut]
    yesterday = (today - timedelta(days=1)).isoformat()
    last = days[-1] if days else None
    last_scored = scored[-1] if scored else None

    warm_fit = fit_warm_day(days, today)
    warm_days = {}
    for day, fc in sorted((forecast or {}).items()):
        fc = fc if isinstance(fc, dict) else {"high": fc}
        if day >= today.isoformat() and warm_fit and fc.get("high") is not None:
            warm_days[day] = {
                "forecast_high": round(fc["high"], 1),
                "indoor_peak": predict_peak(warm_fit, fc["high"]),
                "condition": fc.get("condition"),
                "sunny": fc.get("condition") in SUNNY_CONDITIONS if fc.get("condition") else None,
                "uv_index": fc.get("uv_index"),
            }
    last_peak = None
    if last and last.get("indoor_peak") is not None:
        last_peak = {"day": last["day"], "indoor_peak": last["indoor_peak"], "out_high": last.get("out_high")}
        if warm_fit and last.get("out_high") is not None:
            last_peak["predicted_peak"] = predict_peak(warm_fit, last["out_high"])

    return {
        "warm_fit": warm_fit,
        "warm_days": warm_days,
        "last_peak": last_peak,
        "fit": fit,
        "rolling_fit": rolling,
        "seasons": seasons,
        "last_day": last,
        "last_day_scored": last_scored,
        "last_day_is_yesterday": bool(last and last["day"] == yesterday),
        "unusual_recent": unusual,
        "days_recorded": len(days),
        "points": [
            {"d": d["day"], "x": d["hdd"], "y": d["heat_min"],
             **({"e": d["expected_min"], "u": d["unusual"]} if fit else {})}
            for d in (scored or days)[-120:]
        ],
    }


# ------------------------------------------------------------- warm days
#
# How warm the house gets on its own: daily indoor peak vs the outdoor high.
# Sun and mild air carry the house past the heating target on warm days, so
# the morning heat-up can aim lower and let the day finish the job.

# Forecast conditions under which the sun can be counted on. Checked against
# 2026-10/11 history: UV index adds nothing over the outdoor high yet (it barely
# varies in autumn), so sunshine gates the ease-off instead of entering the fit.
SUNNY_CONDITIONS = frozenset({"sunny", "clear", "partlycloudy", "clear-night", "windy"})
WARM_FIT_DAYS = 60  # sun angle and habits drift; prefer recent days
MIN_WARM_FIT_DAYS = 8


def daily_highs(records: Iterable[dict[str, Any]], tz: tzinfo) -> dict[str, float]:
    """Max outdoor temperature per local day from history/resampled records."""
    out: dict[str, float] = {}
    for r in records:
        ts = r.get("timestamp") or r.get("last_updated") or r.get("last_changed")
        if ts is None:
            continue
        attrs = r.get("attributes") or {}
        temp = r.get("state")
        try:
            temp = float(temp)
        except (TypeError, ValueError):
            temp = attrs.get("temperature")
            if not isinstance(temp, (int, float)):
                continue
        day = _as_dt(ts).astimezone(tz).date().isoformat()
        out[day] = max(out.get(day, temp), float(temp))
    return out


def merge_highs(stored: dict[str, float], fresh: dict[str, float]) -> dict[str, float]:
    merged = dict(stored)
    for day, high in fresh.items():
        merged[day] = round(max(merged.get(day, high), high), 1)
    return dict(sorted(merged.items()))


def fit_warm_day(days: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    """Least squares indoor_peak = intercept + slope * out_high (no AC days)."""
    usable = [d for d in days
              if d.get("indoor_peak") is not None and d.get("out_high") is not None
              and d.get("cool_min", 0) < 1]
    cutoff = (today - timedelta(days=WARM_FIT_DAYS)).isoformat()
    recent = [d for d in usable if d["day"] >= cutoff]
    pts = [(d["out_high"], d["indoor_peak"]) for d in (recent if len(recent) >= MIN_WARM_FIT_DAYS else usable)]
    n = len(pts)
    if n < MIN_WARM_FIT_DAYS:
        return None
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx <= 1e-9:
        return None
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in pts]
    ss_tot = sum((y - my) ** 2 for _, y in pts)
    return {
        "slope": round(slope, 3),
        "intercept": round(intercept, 2),
        "r2": round(1 - sum(r * r for r in resid) / ss_tot, 3) if ss_tot > 0 else 0.0,
        "days": n,
        "error_f": round(math.sqrt(sum(r * r for r in resid) / max(n - 2, 1)), 2),
        "high_range": [round(min(x for x, _ in pts)), round(max(x for x, _ in pts))],
    }


def predict_peak(fit: dict[str, Any], high: float) -> float:
    return round(fit["intercept"] + fit["slope"] * high, 1)


# ---------------------------------------------------------------- warm-ups


def recovery_entry(event_data: dict[str, Any], ts: datetime) -> dict[str, Any] | None:
    """Turn an ``ihvac_recovery_measured`` event into a log row (None for resets)."""
    try:
        predicted = float(event_data["predicted_min"])
        actual = float(event_data["actual_min"])
    except (KeyError, TypeError, ValueError):
        return None
    if predicted <= 0 or actual <= 0:
        return None
    row: dict[str, Any] = {
        "ts": ts.isoformat(),
        "predicted_min": round(predicted, 1),
        "actual_min": round(actual, 1),
        "error_min": round(actual - predicted, 1),
        "ratio": round(actual / predicted, 3),
    }
    for key in ("outdoor_f", "start_indoor_f", "target_f"):
        try:
            row[key] = round(float(event_data[key]), 1)
        except (KeyError, TypeError, ValueError):
            pass
    return row


def append_recovery(log: list[dict[str, Any]], row: dict[str, Any]) -> list[dict[str, Any]]:
    return (log + [row])[-MAX_RECOVERY_LOG:]


def recovery_bias(log: list[dict[str, Any]]) -> dict[str, Any]:
    """Signed error (+ = took longer than predicted) over the recent runs."""
    recent = log[-BIAS_WINDOW_RUNS:]
    n = len(recent)
    out: dict[str, Any] = {"runs": n, "runs_total": len(log), "verdict": "learning"}
    if not recent:
        return out
    errors = [r["error_min"] for r in recent]
    mean_err = sum(errors) / n
    mean_pred = sum(r["predicted_min"] for r in recent) / n
    out.update({
        "mean_error_min": round(mean_err, 1),
        "mae_min": round(sum(abs(e) for e in errors) / n, 1),
        "mean_ratio": round(sum(r["ratio"] for r in recent) / n, 3),
        "late_runs": sum(1 for e in errors if e > 0),
        "early_runs": sum(1 for e in errors if e < 0),
    })
    if n >= MIN_BIAS_RUNS:
        threshold = max(5.0, 0.15 * mean_pred)
        if mean_err > threshold:
            out["verdict"] = "runs_late"  # heat-ups take longer: model starts too late
        elif mean_err < -threshold:
            out["verdict"] = "runs_early"  # heat-ups are faster: model starts too early
        else:
            out["verdict"] = "on_track"
    return out
