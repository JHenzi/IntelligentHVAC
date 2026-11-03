#!/usr/bin/env python3
"""Replay exported recorder history through the integration's pipeline, offline.

    python dev/replay.py export.json [--climate climate.thermostat]
        [--indoor sensor.bedroom_temperature] [--outdoor sensor.openweathermap_temperature]
        [--updates 96]

The export is {entity_id: [{"t": unix_ts, "state": ..., "attrs": {...}}, ...]}.
Needs homeassistant importable (e.g. the pytest container). Runs a first full
fetch, then N simulated 15-minute incremental updates over the last day, to
show the segment store stays duplicate-free.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.intelligent_hvac.resampling import resample_to_uniform_timesteps  # noqa: E402
from custom_components.intelligent_hvac.segmentation import identify_segments  # noqa: E402


def load(path):
    raw = json.load(open(path))
    hist = {}
    for eid, rows in raw.items():
        hist[eid] = []
        for r in rows:
            t = datetime.fromtimestamp(r["t"], tz=timezone.utc)
            hist[eid].append({"state": r["state"], "last_changed": t, "last_updated": t, "attributes": r["attrs"]})
    return hist


def window(hist, start, end):
    """Like the recorder: records in [start, end] plus the state in effect at start."""
    out = {}
    for eid, rows in hist.items():
        before = [r for r in rows if r["last_updated"] <= start]
        inside = [r for r in rows if start < r["last_updated"] <= end]
        out[eid] = before[-1:] + inside
    return out


def segments(hist, start, end, climate, indoor, outdoor):
    w = window(hist, start, end)
    res = {e: resample_to_uniform_timesteps(r, start, end, 5, entity_id=e) for e, r in w.items() if r}
    return identify_segments(res, climate, [climate, indoor], outdoor, 5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("--climate", default="climate.thermostat")
    ap.add_argument("--indoor", default="sensor.bedroom_temperature")
    ap.add_argument("--outdoor", default="sensor.openweathermap_temperature")
    ap.add_argument("--updates", type=int, default=96)
    a = ap.parse_args()

    hist = load(a.export)
    last = max(r["last_updated"] for r in hist[a.climate])
    first = min(r["last_updated"] for r in hist[a.climate])
    end = last - timedelta(days=1)

    store = {}
    for s in segments(hist, first, end, a.climate, a.indoor, a.outdoor):
        store[(s.start_time, s.type)] = s
    n_first = len(store)
    added = 0
    for i in range(1, a.updates + 1):
        now = end + timedelta(minutes=15 * i)
        for s in segments(hist, now - timedelta(hours=24), now, a.climate, a.indoor, a.outdoor):
            key = (s.start_time, s.type)
            added += key not in store
            store[key] = s

    segs = sorted(store.values(), key=lambda s: s.start_time)
    print(f"history {first:%m-%d %H:%M} .. {last:%m-%d %H:%M}")
    print(f"first fetch: {n_first} segments; {a.updates} incremental updates added {added} new")
    overlaps = sum(1 for x, y in zip(segs, segs[1:]) if y.start_time < x.end_time)
    print(f"store: {len(segs)} segments, overlapping neighbours: {overlaps}")
    print("by type:", dict(collections.Counter(s.type for s in segs)))
    heat = [s for s in segs if s.type == "Active_Heating"]
    if heat:
        mins = sorted(s.duration_minutes for s in heat)
        print(f"heating runs: {len(heat)}, duration median {mins[len(mins)//2]:.0f} min, "
              f"total {sum(mins)/60:.1f} h")
        for s in [s for s in heat if s.total_temp_change >= 2]:
            print(f"   {s.start_time:%m-%d %H:%M}  {s.duration_minutes:4.0f} min  "
                  f"{s.start_indoor_temp:.1f}->{s.end_indoor_temp:.1f}  out {s.avg_outdoor_temp:5.1f}  "
                  f"{s.rate:+.1f}°F/h")


if __name__ == "__main__":
    main()
