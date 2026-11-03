"""First-order ("lumped RC") house model for heating.

    dTi/dt = P·u − k·(Ti − To) + g        [°F/h]

    k  heat-loss coefficient [1/h]: how fast the house leaks toward outdoors
    g  free gains [°F/h]: people, appliances, sun (averaged)
    P  heating power [°F/h]: how fast the furnace alone would raise the house
    u  1 while heating, 0 while idle

Fitting:
  * k, g from idle runs (u = 0): weighted least squares of rate on (Ti − To).
  * P from sustained heating runs (rise ≥ 1°F, ≥ 20 min):
        P = rate + k·(Ti − To) − g
    and, robust to a thermostat's 1°F resolution, from duty cycle over 4-hour
    windows:  P = (ΔTi/Δt + k·(Ti − To) − g) / heating_fraction.

Recovery from T1 to T2 at outdoor To, heating continuously, has the closed form

    Teq = To + (P + g)/k          (temperature the furnace could hold at most)
    t   = ln((Teq − T1) / (Teq − T2)) / k

so colder outdoor air means slower recovery *by construction*, which the
previous regression got backwards. Pure Python, no Home Assistant imports.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterable

from .const import (
    SEGMENT_TYPE_ACTIVE_HEATING,
    SEGMENT_TYPE_PASSIVE_DECAY,
    SEGMENT_TYPE_PASSIVE_GAIN,
)
from .models import ThermalSegment

IDLE_TYPES = (SEGMENT_TYPE_PASSIVE_DECAY, SEGMENT_TYPE_PASSIVE_GAIN)

MIN_IDLE_MINUTES = 60          # shorter idle runs are dominated by furnace/sensor lag
MIN_IDLE_RUNS = 8
K_RANGE = (0.003, 0.5)         # 1/h; outside this the fit is not a house
P_RANGE = (1.0, 40.0)          # °F/h
LONG_RUN_MINUTES = 20
LONG_RUN_RISE_F = 1.0
DUTY_WINDOW = timedelta(hours=4)
DUTY_MIN_COVERAGE = 0.8
DUTY_FRACTION_RANGE = (0.05, 0.95)
MIN_LONG_RUNS_FOR_P = 3


@dataclass
class PhysicsModel:
    k: float
    g: float
    P: float
    n_idle: int
    n_long: int
    n_duty: int
    p_source: str                       # "long_runs" | "duty_cycle"
    outdoor_range: tuple[float, float]
    backtest_mae_min: float | None = None
    backtest_bias_min: float | None = None
    backtest_n: int = 0
    notes: list[str] = field(default_factory=list)

    # --- predictions -----------------------------------------------------
    def equilibrium(self, outdoor: float) -> float:
        """Highest indoor temperature the furnace can hold at this outdoor temp."""
        return outdoor + (self.P + self.g) / self.k

    def recovery_minutes(self, t_from: float, t_to: float, outdoor: float) -> float | None:
        """Minutes of continuous heating from t_from to t_to. None if unreachable."""
        return recovery_minutes(t_from, t_to, outdoor, self.k, self.g, self.P)

    def passive_rate(self, indoor: float, outdoor: float) -> float:
        """°F/h with the HVAC idle (negative = cooling down)."""
        return -self.k * (indoor - outdoor) + self.g

    def as_params(self) -> dict:
        return {
            "k": self.k, "g": self.g, "P": self.P,
            "n_idle": self.n_idle, "n_long": self.n_long, "n_duty": self.n_duty,
            "p_source": self.p_source,
            "outdoor_min": self.outdoor_range[0], "outdoor_max": self.outdoor_range[1],
            "backtest_mae_min": self.backtest_mae_min, "backtest_bias_min": self.backtest_bias_min,
            "backtest_n": self.backtest_n, "notes": self.notes,
        }

    @classmethod
    def from_params(cls, p: dict) -> "PhysicsModel":
        return cls(
            k=p["k"], g=p["g"], P=p["P"], n_idle=p.get("n_idle", 0), n_long=p.get("n_long", 0),
            n_duty=p.get("n_duty", 0), p_source=p.get("p_source", ""),
            outdoor_range=(p.get("outdoor_min", 0.0), p.get("outdoor_max", 0.0)),
            backtest_mae_min=p.get("backtest_mae_min"), backtest_bias_min=p.get("backtest_bias_min"),
            backtest_n=p.get("backtest_n", 0), notes=list(p.get("notes", [])),
        )


def recovery_minutes(t_from: float, t_to: float, outdoor: float, k: float, g: float, P: float) -> float | None:
    if t_to <= t_from:
        return 0.0
    teq = outdoor + (P + g) / k
    if t_to >= teq - 0.05:
        return None  # the furnace can't get there at this outdoor temperature
    return math.log((teq - t_from) / (teq - t_to)) / k * 60.0


# --- fitting ---------------------------------------------------------------

def _mid(seg: ThermalSegment) -> float:
    return (seg.start_indoor_temp + seg.end_indoor_temp) / 2.0


def _wls(xs: list[float], ys: list[float], ws: list[float]) -> tuple[float, float] | None:
    """Weighted least squares y = a·x + b. Returns (a, b)."""
    sw = sum(ws)
    if sw <= 0:
        return None
    mx = sum(w * x for w, x in zip(ws, xs)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    if sxx <= 1e-9:
        return None
    a = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / sxx
    return a, my - a * mx


def fit_loss(segments: Iterable[ThermalSegment]) -> tuple[float, float, int] | None:
    idle = [s for s in segments if s.type in IDLE_TYPES and s.duration_minutes >= MIN_IDLE_MINUTES
            and s.avg_outdoor_temp is not None]
    if len(idle) < MIN_IDLE_RUNS:
        return None
    fit = _wls([_mid(s) - s.avg_outdoor_temp for s in idle], [s.rate for s in idle],
               [s.duration_minutes for s in idle])
    if fit is None:
        return None
    slope, g = fit
    k = -slope
    if not (K_RANGE[0] <= k <= K_RANGE[1]):
        return None
    return k, g, len(idle)


def long_runs(segments: Iterable[ThermalSegment]) -> list[ThermalSegment]:
    return [s for s in segments if s.type == SEGMENT_TYPE_ACTIVE_HEATING
            and s.duration_minutes >= LONG_RUN_MINUTES and s.total_temp_change >= LONG_RUN_RISE_F
            and s.avg_outdoor_temp is not None]


def _p_from_run(s: ThermalSegment, k: float, g: float) -> float:
    return s.rate + k * (_mid(s) - s.avg_outdoor_temp) - g


def duty_cycle_estimates(segments: list[ThermalSegment], k: float, g: float) -> list[float]:
    """P estimates from 4-hour windows of consecutive runs containing heating."""
    segs = sorted(segments, key=lambda s: s.start_time)
    out: list[float] = []
    i = 0
    while i < len(segs):
        start = segs[i].start_time
        j = i
        while j + 1 < len(segs) and segs[j + 1].end_time <= start + DUTY_WINDOW:
            j += 1
        win = segs[i:j + 1]
        i = j + 1
        span_h = (win[-1].end_time - win[0].start_time).total_seconds() / 3600
        if span_h < DUTY_WINDOW.total_seconds() / 3600 * DUTY_MIN_COVERAGE:
            continue
        covered_h = sum(s.duration_minutes for s in win) / 60
        if covered_h < span_h * DUTY_MIN_COVERAGE:
            continue  # big gaps: unknown what the furnace did
        heat_h = sum(s.duration_minutes for s in win if s.type == SEGMENT_TYPE_ACTIVE_HEATING) / 60
        frac = heat_h / span_h
        if not (DUTY_FRACTION_RANGE[0] <= frac <= DUTY_FRACTION_RANGE[1]):
            continue
        outs = [s.avg_outdoor_temp for s in win if s.avg_outdoor_temp is not None]
        if not outs:
            continue
        ti = statistics.fmean(_mid(s) for s in win)
        to = statistics.fmean(outs)
        net_rate = (win[-1].end_indoor_temp - win[0].start_indoor_temp) / span_h
        p = (net_rate + k * (ti - to) - g) / frac
        if P_RANGE[0] <= p <= P_RANGE[1]:
            out.append(p)
    return out


def fit_heating(segments: list[ThermalSegment]) -> PhysicsModel | None:
    loss = fit_loss(segments)
    if loss is None:
        return None
    k, g, n_idle = loss
    runs = long_runs(segments)
    p_long = [p for p in (_p_from_run(s, k, g) for s in runs) if P_RANGE[0] <= p <= P_RANGE[1]]
    p_duty = duty_cycle_estimates(segments, k, g)
    notes = []
    if len(p_long) >= MIN_LONG_RUNS_FOR_P:
        P, source = statistics.median(p_long), "long_runs"
    elif p_duty:
        P, source = statistics.median(p_duty), "duty_cycle"
        notes.append("few sustained heating runs; heating power from duty cycle")
    else:
        return None
    outs = [s.avg_outdoor_temp for s in segments if s.avg_outdoor_temp is not None]
    model = PhysicsModel(k=k, g=g, P=P, n_idle=n_idle, n_long=len(p_long), n_duty=len(p_duty),
                         p_source=source, outdoor_range=(min(outs), max(outs)), notes=notes)
    _backtest(model, segments, runs)
    return model


def _backtest(model: PhysicsModel, segments: list[ThermalSegment], runs: list[ThermalSegment]) -> None:
    """Leave-one-out: predict each sustained heating run's duration without it."""
    if len(runs) < 2:
        return
    errors = []
    for held in runs:
        others = [_p_from_run(s, model.k, model.g) for s in runs if s is not held]
        others = [p for p in others if P_RANGE[0] <= p <= P_RANGE[1]]
        if len(others) >= MIN_LONG_RUNS_FOR_P - 1:
            P = statistics.median(others)
        elif model.n_duty:
            P = statistics.median(duty_cycle_estimates(segments, model.k, model.g))
        else:
            continue
        pred = recovery_minutes(held.start_indoor_temp, held.end_indoor_temp, held.avg_outdoor_temp,
                                model.k, model.g, P)
        if pred is not None:
            errors.append(pred - held.duration_minutes)
    if errors:
        model.backtest_n = len(errors)
        model.backtest_mae_min = statistics.fmean(abs(e) for e in errors)
        model.backtest_bias_min = statistics.fmean(errors)


def confidence(model: PhysicsModel | None) -> float:
    """0..1. Driven by measured prediction error, then by how much data backs it."""
    if model is None:
        return 0.0
    coverage = 0.5 * min(1.0, model.n_idle / 30) + 0.5 * min(1.0, (model.n_long + model.n_duty / 3) / 10)
    if model.backtest_mae_min is not None and model.backtest_n >= 3:
        accuracy = max(0.0, 1.0 - model.backtest_mae_min / 30.0)   # 0 min error -> 1, 30 min -> 0
    else:
        accuracy = 0.3   # not yet verified against real recoveries
    return round(0.65 * accuracy + 0.35 * coverage, 3)
