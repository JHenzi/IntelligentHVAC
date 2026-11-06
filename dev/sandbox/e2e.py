#!/usr/bin/env python3
"""End-to-end checks of the comfort package + integration against the sandbox HA.

    dev/sandbox/prepare.sh && (cd dev/sandbox && docker compose up -d)
    python3 dev/sandbox/e2e.py [--url http://localhost:8125] [--restart]

Stdlib only. Onboards the throw-away instance on first run (credentials are
sandbox-only and kept in dev/sandbox/config/, which is git-ignored).
Exit code is the number of failed checks.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

TOKEN_FILE = Path(__file__).resolve().parent / "config" / ".e2e_token.json"
CLIMATE = "climate.hvac"
WEATHER = "weather.demo_weather_south"

failures: list[str] = []


class HA:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.client_id = self.url + "/"
        self.token = ""

    # --- transport -------------------------------------------------------
    def _req(self, method, path, body=None, form=False, auth=True, timeout=60):
        headers = {}
        data = None
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body=None, timeout=60):
        return self._req("POST", path, body if body is not None else {}, timeout=timeout)

    # --- auth --------------------------------------------------------------
    def login(self) -> None:
        try:
            onboarding = self._req("GET", "/api/onboarding", auth=False)
            user_done = next(s for s in onboarding if s["step"] == "user")["done"]
        except urllib.error.HTTPError:
            user_done = True  # endpoint goes away once onboarding is finished
        if not user_done:
            code = self._req(
                "POST", "/api/onboarding/users", auth=False,
                body={"client_id": self.client_id, "name": "Sandbox", "username": "sandbox",
                      "password": "sandbox-only-password", "language": "en"},
            )["auth_code"]
            tok = self._req("POST", "/auth/token", form=True, auth=False,
                            body={"grant_type": "authorization_code", "code": code,
                                  "client_id": self.client_id})
            TOKEN_FILE.write_text(json.dumps({"refresh_token": tok["refresh_token"]}))
            self.token = tok["access_token"]
            # Finish onboarding so the UI is usable at the sandbox URL.
            for step in ("core_config", "analytics", "integration"):
                body = {"client_id": self.client_id, "redirect_uri": self.client_id} if step == "integration" else {}
                try:
                    self.post(f"/api/onboarding/{step}", body)
                except urllib.error.HTTPError:
                    pass
            return
        if not TOKEN_FILE.exists():
            sys.exit(f"Sandbox already onboarded but {TOKEN_FILE} is missing; "
                     "delete dev/sandbox/config/.storage and restart the sandbox.")
        refresh = json.loads(TOKEN_FILE.read_text())["refresh_token"]
        tok = self._req("POST", "/auth/token", form=True, auth=False,
                        body={"grant_type": "refresh_token", "refresh_token": refresh,
                              "client_id": self.client_id})
        self.token = tok["access_token"]

    # --- helpers -----------------------------------------------------------
    def state(self, entity_id):
        try:
            return self.get(f"/api/states/{entity_id}")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise

    def call(self, domain, service, timeout=60, **data):
        return self.post(f"/api/services/{domain}/{service}", data, timeout=timeout)

    def set_number(self, entity_id, value):
        self.call("input_number", "set_value", entity_id=entity_id, value=value)

    def automation_by_id(self, auto_id):
        for st in self.get("/api/states"):
            if st["entity_id"].startswith("automation.") and st["attributes"].get("id") == auto_id:
                return st["entity_id"]
        raise LookupError(auto_id)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail and not ok else ''}")
    if not ok:
        failures.append(name)


def wait_up(ha: HA, timeout=180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ha._req("GET", "/api/", auth=False, timeout=5)
            return
        except urllib.error.HTTPError:
            return  # 401 = HTTP server is up
        except Exception:
            time.sleep(2)
    sys.exit("sandbox HA did not come up")


def wait_running(ha: HA, timeout=180) -> None:
    """HTTP answers before integrations are loaded; wait for core to be RUNNING."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if ha.get("/api/config").get("state") == "RUNNING":
                return
        except Exception:
            pass
        time.sleep(2)
    sys.exit("sandbox HA never reached RUNNING")


def settle(seconds=1.5):
    time.sleep(seconds)


# ---------------------------------------------------------------------------
def ensure_integration(ha: HA) -> None:
    print("Integration")
    entries = ha.get("/api/config/config_entries/entry?domain=intelligent_hvac")
    if not entries:
        flow = ha.post("/api/config/config_entries/flow", {"handler": "intelligent_hvac"})
        check("config flow shows user form", flow.get("type") == "form", str(flow))
        bad = ha.post(f"/api/config/config_entries/flow/{flow['flow_id']}",
                      {"climate_entity": "climate.does_not_exist", "indoor_sensors": ["climate.ecobee"],
                       "outdoor_sensor": "sensor.sandbox_outdoor_temperature"})
        check("config flow rejects unknown climate entity",
              bad.get("errors", {}).get("climate_entity") == "entity_not_found", str(bad))
        result = ha.post(f"/api/config/config_entries/flow/{flow['flow_id']}",
                         {"climate_entity": CLIMATE, "indoor_sensors": ["climate.ecobee"],
                          "outdoor_sensor": "sensor.sandbox_outdoor_temperature"})
        check("config flow creates entry", result.get("type") == "create_entry", str(result))
        settle(5)
    entries = ha.get("/api/config/config_entries/entry?domain=intelligent_hvac")
    check("entry loaded", bool(entries) and entries[0]["state"] == "loaded",
          entries[0]["state"] if entries else "no entry")
    # The comfort package and dashboard reference these default IDs.
    for eid in ("sensor.intelligent_hvac_thermal_model_status",
                "sensor.intelligent_hvac_time_to_target_5degf_heat",
                "sensor.intelligent_hvac_cooling_strategy"):
        check(f"{eid} exists", ha.state(eid) is not None)


def expected_start(wake: str, minutes: float) -> str:
    t = datetime.strptime(wake, "%H:%M:%S") - timedelta(minutes=minutes)
    return t.strftime("%H:%M:%S")


def recovery_math(ha: HA) -> None:
    print("Recovery math (script.intelligent_hvac_update_recovery)")
    for eid, v in {
        "input_number.target_temperature": 70,
        "input_number.recovery_time_buffer": 5,
        "input_number.nighttime_setback_temperature": 65,
        "input_number.moderate_cold_setback_temperature": 67,
        "input_number.extreme_cold_setback_temperature": 68,
        "input_number.moderate_cold_threshold": 32,
        "input_number.extreme_cold_threshold": 15,
        "input_number.sandbox_time_to_target_heat": 180.3,
        "input_number.fallback_recovery_lead": 90,   # must NOT floor the model
        "input_number.max_recovery_lead": 360,
    }.items():
        ha.set_number(eid, v)
    ha.call("input_datetime", "set_datetime", entity_id="input_datetime.target_wake_up_time", time="09:00:00")

    #            outdoor, target, setback used, expected scaled minutes
    cases = [
        ("mild",    50, 70, 65, round(180.3 * 5 / 5 + 5)),   # plan example: 185 -> 05:55
        ("moderate", 20, 70, 67, round(180.3 * 3 / 5 + 5)),
        ("extreme",  5, 70, 68, round(180.3 * 2 / 5 + 5)),
        ("no delta", 50, 65, 65, 5),   # just the buffer; fallback lead doesn't floor it
    ]
    for name, outdoor, target, setback, scaled in cases:
        ha.set_number("input_number.sandbox_outdoor_temp", outdoor)
        ha.set_number("input_number.target_temperature", target)
        settle()  # let the on-change automation finish so the script isn't "already running"
        ha.call("script", "intelligent_hvac_update_recovery")
        settle(0.5)
        got_scaled = float(ha.state("input_number.scaled_recovery_time")["state"])
        got_delta = float(ha.state("input_number.temperature_delta")["state"])
        got_start = ha.state("input_datetime.smart_recovery_start_time")["state"]
        want_start = expected_start("09:00:00", scaled)
        check(f"{name}: delta {abs(target - setback)}°F", got_delta == abs(target - setback), f"got {got_delta}")
        check(f"{name}: scaled {scaled} min", got_scaled == scaled, f"got {got_scaled}")
        check(f"{name}: start {want_start}", got_start == want_start, f"got {got_start}")
    ha.set_number("input_number.target_temperature", 70)

    # Model answer is capped at max lead; fallback lead is used only without a model.
    def lead_with(fallback, max_lead, target):
        ha.set_number("input_number.fallback_recovery_lead", fallback)
        ha.set_number("input_number.max_recovery_lead", max_lead)
        ha.set_number("input_number.sandbox_outdoor_temp", 50)
        ha.set_number("input_number.target_temperature", target)
        settle()
        ha.call("script", "intelligent_hvac_update_recovery")
        settle(0.5)
        return (float(ha.state("input_number.scaled_recovery_time")["state"]),
                ha.state("input_datetime.smart_recovery_start_time")["state"])

    got = lead_with(30, 150, 70)
    check("clamp: 185 min capped at max lead 150 -> 06:30", got == (150.0, "06:30:00"), str(got))
    got = lead_with(120, 360, 65)
    check("fast model is not floored by fallback lead 120 -> 08:55", got == (5.0, "08:55:00"), str(got))
    got = lead_with(200, 150, 70)
    check("max lead caps the model even under a larger fallback lead", got[0] == 150.0, str(got))
    # The live bug: 43.5 min/5°F model, 90 min fallback -> must be 48.5, not 90.
    ha.set_number("input_number.sandbox_time_to_target_heat", 43.5)
    got = lead_with(90, 180, 70)
    check("model 43.5 + buffer 5 -> 48 min, start 08:12 (not pinned to 07:30)",
          got in ((48.0, "08:12:00"), (49.0, "08:11:00")), str(got))
    # No usable model -> fallback lead.
    ha.set_number("input_number.sandbox_time_to_target_heat", 0)
    got = lead_with(90, 180, 70)
    check("no model -> fallback lead 90 -> 07:30", got == (90.0, "07:30:00"), str(got))
    ha.set_number("input_number.sandbox_time_to_target_heat", 180.3)
    ha.set_number("input_number.fallback_recovery_lead", 30)
    ha.set_number("input_number.max_recovery_lead", 360)
    ha.set_number("input_number.target_temperature", 70)

    outdoor = ha.state("sensor.outdoor_temperature_most_recent")
    check("outdoor template follows source", outdoor and outdoor["state"] == "50.0", outdoor and outdoor["state"])


def heating_automations(ha: HA) -> None:
    print("Heating automations")
    setback = ha.automation_by_id("ihvac_nighttime_setback")
    heatup = ha.automation_by_id("ihvac_morning_heat_up")

    ha.call("climate", "set_hvac_mode", entity_id=CLIMATE, hvac_mode="heat")
    for outdoor, want in ((50, 65), (20, 67), (5, 68)):
        ha.set_number("input_number.sandbox_outdoor_temp", outdoor)
        ha.call("climate", "set_temperature", entity_id=CLIMATE, temperature=70)  # daytime setpoint
        settle()
        ha.call("automation", "trigger", entity_id=setback, skip_condition=False)
        settle()
        got = ha.state(CLIMATE)["attributes"].get("temperature")
        check(f"setback at {outdoor}°F outdoor -> {want}", got is not None and abs(got - want) < 0.6, f"got {got}")

    # Already set below the setback by hand: setback must not raise it.
    ha.set_number("input_number.sandbox_outdoor_temp", 50)
    ha.call("climate", "set_temperature", entity_id=CLIMATE, temperature=62)
    settle()
    ha.call("automation", "trigger", entity_id=setback, skip_condition=False)
    settle()
    got = ha.state(CLIMATE)["attributes"].get("temperature")
    check("setback never raises a lower manual setpoint (62 stays 62)", got is not None and abs(got - 62) < 0.6, f"got {got}")

    ha.call("automation", "trigger", entity_id=heatup, skip_condition=False)
    settle()
    got = ha.state(CLIMATE)["attributes"].get("temperature")
    check("morning heat-up raises to target in heat mode", got is not None and abs(got - 70) < 0.6, f"got {got}")

    ha.call("climate", "set_hvac_mode", entity_id=CLIMATE, hvac_mode="cool")
    ha.call("climate", "set_temperature", entity_id=CLIMATE, temperature=76)
    settle()
    ha.call("automation", "trigger", entity_id=heatup, skip_condition=False)
    settle()
    got = ha.state(CLIMATE)["attributes"].get("temperature")
    check("morning heat-up does nothing outside heat mode", got is not None and abs(got - 76) < 0.6, f"got {got}")


def fallback_heat_up(ha: HA) -> None:
    print("Fallback heat-up")
    auto = ha.automation_by_id("ihvac_fallback_heat_up")
    # Wake-up times are HA-local; the machine running this may be in another zone (CI = UTC).
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo(ha.get("/api/config")["time_zone"]))

    def run(wake_in_min, start_ago_min, mode="heat", setpoint=62):
        wake = (now + timedelta(minutes=wake_in_min)).strftime("%H:%M:00")
        start = (now - timedelta(minutes=start_ago_min)).strftime("%H:%M:00")
        ha.call("input_datetime", "set_datetime", entity_id="input_datetime.target_wake_up_time", time=wake)
        ha.call("input_datetime", "set_datetime", entity_id="input_datetime.smart_recovery_start_time", time=start)
        ha.set_number("input_number.fallback_recovery_lead", 60)
        ha.set_number("input_number.target_temperature", 70)
        ha.call("climate", "set_hvac_mode", entity_id=CLIMATE, hvac_mode=mode)
        ha.call("climate", "set_temperature", entity_id=CLIMATE, temperature=setpoint)
        settle()
        ha.call("automation", "trigger", entity_id=auto, skip_condition=False)
        settle()
        return ha.state(CLIMATE)["attributes"].get("temperature")

    if now.hour < 1 or now.hour >= 22:
        print("  SKIP  too close to midnight for wake-time arithmetic")
        return
    # Wake in 30 min with a 60 min fallback lead: the old fixed window would
    # fire here, but the smart start (in 10 min) hasn't come yet.
    got = run(wake_in_min=30, start_ago_min=-10)
    check("smart start still ahead (inside old fixed window): no change", abs(got - 62) < 0.6, f"got {got}")
    got = run(wake_in_min=30, start_ago_min=2)
    check("smart start 2 min ago (grace): no change", abs(got - 62) < 0.6, f"got {got}")
    got = run(wake_in_min=30, start_ago_min=15, mode="cool", setpoint=76)
    check("start missed but not heat mode: no change", abs(got - 76) < 0.6, f"got {got}")
    got = run(wake_in_min=30, start_ago_min=15)
    check("start missed 15 min ago, setpoint low: raised to target", abs(got - 70) < 0.6, f"got {got}")
    got = run(wake_in_min=30, start_ago_min=15, setpoint=70)
    check("start missed, already at target: no change", abs(got - 70) < 0.6, f"got {got}")
    ha.call("input_datetime", "set_datetime", entity_id="input_datetime.target_wake_up_time", time="09:00:00")
    ha.set_number("input_number.fallback_recovery_lead", 30)


def season_switch(ha: HA) -> None:
    print("Season switch (forecast)")
    auto = ha.automation_by_id("ihvac_set_mode_by_forecast")
    resp = ha.post("/api/services/weather/get_forecasts?return_response",
                   {"entity_id": WEATHER, "type": "daily"})
    days = resp["service_response"][WEATHER]["forecast"]
    high = float(days[0]["temperature"])
    print(f"        (demo forecast high today: {high}°F)")

    def run(cool_above, heat_below, start_mode, want):
        ha.call("climate", "set_hvac_mode", entity_id=CLIMATE, hvac_mode=start_mode)
        ha.set_number("input_number.forecast_cool_above", cool_above)
        ha.set_number("input_number.forecast_heat_below", heat_below)
        settle(0.5)
        ha.call("automation", "trigger", entity_id=auto, skip_condition=False)
        settle()
        return ha.state(CLIMATE)["state"]

    if 60 <= high - 1 <= 100:
        got = run(high - 1, 30, "heat", "cool")
        check("forecast above threshold -> cool", got == "cool", f"got {got}")
    if 30 <= high + 1 <= 75:
        got = run(100, high + 1, "cool", "heat")
        check("forecast below threshold -> heat", got == "heat", f"got {got}")
    if 60 <= high + 1 <= 100 and 30 <= high - 1 <= 75:
        got = run(high + 1, high - 1, "heat", "heat")
        check("forecast between thresholds -> unchanged", got == "heat", f"got {got}")
    last = ha.state(auto)["attributes"].get("last_triggered")
    check("forecast automation ran", last is not None)


def restart_persistence(ha: HA) -> None:
    print("Helpers survive restart (no initial:)")
    ha.set_number("input_number.target_temperature", 71)
    ha.set_number("input_number.recovery_time_buffer", 17)
    settle()
    try:
        ha.call("homeassistant", "restart")
    except Exception:
        pass
    time.sleep(10)
    wait_up(ha)
    ha.login()
    wait_running(ha)
    check("target_temperature persisted", ha.state("input_number.target_temperature")["state"] == "71.0")
    check("recovery_time_buffer persisted", ha.state("input_number.recovery_time_buffer")["state"] == "17.0")
    ha.set_number("input_number.target_temperature", 70)


def log_errors(since: str) -> None:
    print("Log")
    try:
        out = subprocess.run(["docker", "logs", "--since", since, "ha-sandbox"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        print("  SKIP  docker logs unavailable")
        return
    lines = [ln for ln in (out.stdout + out.stderr).splitlines()
             if "ERROR" in ln and ("intelligent_hvac" in ln or "ihvac" in ln.lower()
                                   or "automation" in ln or "script" in ln or "template" in ln)]
    check("no integration/package errors logged during run", not lines, "\n        ".join(lines[:10]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8125")
    ap.add_argument("--restart", action="store_true", help="also verify helper values survive a restart")
    args = ap.parse_args()

    since = datetime.now().astimezone().isoformat(timespec="seconds")
    ha = HA(args.url)
    wait_up(ha)
    ha.login()
    wait_running(ha)

    ensure_integration(ha)
    recovery_math(ha)
    heating_automations(ha)
    fallback_heat_up(ha)
    season_switch(ha)
    if args.restart:
        restart_persistence(ha)
    log_errors(since)

    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
