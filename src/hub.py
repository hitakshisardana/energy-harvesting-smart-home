"""
The MQTT hub -- the Python half of the Wokwi <-> Python closed loop (Phase 3,
M3 milestone S4, M1 milestone M4). This is a GRADED success criterion.

    subscribe  <PREFIX>/+/state
    on NodeState:
        pick a mode from stored_energy_pct
        publish ScheduleCommand to <PREFIX>/<node_id>/schedule

Deliberately dumb: the mapping below is a threshold ladder, no ML. That is the
correct first version -- get the loop closed, THEN insert forecasting and the
scheduler between parse and publish. Nothing else changes when you do, because
the schedulers in schedulers.py already take a forecaster and return a decision.

Message shapes are validated against config/interface.schema.json. If a field
is added or renamed, change the schema in the SAME commit and tell M1.

    pip install paho-mqtt
    python src/hub.py --prefix ehsh-team7                 # live, against Wokwi
    python src/hub.py --prefix ehsh-team7 --record log.jsonl
    python src/hub.py --replay log.jsonl                  # no broker needed
    python src/hub.py --selftest                          # no broker, no Wokwi

`--replay` exists because the demo must survive a flaky public broker. Record a
good session once; replay it if the network dies in front of the examiner.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "config" / "interface.schema.json"

# Mode ladder. Mirrors firmware/node.ino modeFromSoc() -- if you change one,
# change both, or the node and hub will disagree about what MEDIUM means.
LADDER = [(70.0, "HIGH", 5), (40.0, "MEDIUM", 20), (15.0, "LOW", 60)]
CRITICAL = ("CRITICAL", 0)          # no routine sensing; alarm reserve only

TASKS_FOR_MODE = {
    "HIGH":     ["occupancy", "environment", "door", "alarm"],
    "MEDIUM":   ["occupancy", "environment", "alarm"],
    "LOW":      ["occupancy", "alarm"],
    "CRITICAL": ["alarm"],
}

VALID_NODES = {"floor", "door", "furniture", "gym", "ambient"}


def decide_mode(soc_pct: float) -> tuple[str, int]:
    """stored_energy_pct -> (mode, sampling_interval_s)."""
    for floor_pct, mode, interval in LADDER:
        if soc_pct >= floor_pct:
            return mode, interval
    return CRITICAL


def make_schedule(state: dict) -> dict:
    """NodeState -> ScheduleCommand. Pure function: unit-testable, no I/O."""
    mode, interval = decide_mode(float(state["stored_energy_pct"]))
    return {
        "node_id": state["node_id"],
        "mode": mode,
        "sampling_interval_s": interval,
        "task_set": TASKS_FOR_MODE[mode],
        "schedule_ts": int(time.time()),
    }


def validate_state(msg: dict) -> str | None:
    """Cheap structural check against interface.schema.json's NodeState.

    Returns an error string, or None if the message is acceptable. We do not
    pull in jsonschema for this: the contract is five required fields and the
    point is to fail loudly when M1's firmware and this file drift apart.
    """
    for f in ("node_id", "ts", "stored_energy_pct", "mode", "sampling_interval_s"):
        if f not in msg:
            return f"missing required field '{f}'"
    if msg["node_id"] not in VALID_NODES:
        return f"unknown node_id {msg['node_id']!r} (expected one of {sorted(VALID_NODES)})"
    soc = msg["stored_energy_pct"]
    if not isinstance(soc, (int, float)) or not 0 <= soc <= 100:
        return f"stored_energy_pct out of range: {soc!r}"
    return None


def log(*parts):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *parts, flush=True)


# ---------------------------------------------------------------- live loop --
def run_live(prefix: str, host: str, port: int, record: Path | None,
             drive_energy: bool):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        sys.exit("paho-mqtt not installed:  pip install paho-mqtt")

    rec = record.open("a") if record else None
    seen: dict[str, dict] = {}

    def on_connect(client, _u, _f, rc, *_):
        log(f"connected rc={rc}; subscribing {prefix}/+/state")
        client.subscribe(f"{prefix}/+/state", qos=1)

    def on_message(client, _u, m):
        try:
            state = json.loads(m.payload)
        except json.JSONDecodeError:
            log("!! non-JSON payload on", m.topic)
            return
        err = validate_state(state)
        if err:
            log(f"!! {m.topic}: {err} -- schema drift, tell M1")
            return

        seen[state["node_id"]] = state
        cmd = make_schedule(state)
        if drive_energy:
            # Optional: the hub, not the pot, owns the energy number. This is
            # how the Python sim drives the Wokwi node in a full closed run.
            cmd["stored_energy_pct"] = state["stored_energy_pct"]

        topic = f"{prefix}/{state['node_id']}/schedule"
        client.publish(topic, json.dumps(cmd), qos=1)
        log(f"{state['node_id']:<9} soc={state['stored_energy_pct']:5.1f}% "
            f"-> {cmd['mode']:<8} every {cmd['sampling_interval_s']}s")
        if rec:
            rec.write(json.dumps({"t": time.time(), "topic": m.topic,
                                  "state": state, "cmd": cmd}) + "\n")
            rec.flush()

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    log(f"connecting to {host}:{port}")
    client.connect(host, port, keepalive=60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log(f"stopped. nodes seen: {sorted(seen) or 'none'}")
    finally:
        if rec:
            rec.close()


# ------------------------------------------------------------------ replay --
def run_replay(path: Path, speed: float):
    """Re-run a recorded session with no broker. The demo safety net."""
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if not lines:
        sys.exit(f"{path} is empty")
    log(f"replaying {len(lines)} exchanges from {path.name} at {speed}x")
    t0 = lines[0]["t"]
    for i, row in enumerate(lines):
        if i:
            time.sleep(min((row["t"] - lines[i - 1]["t"]) / max(speed, 1e-9), 5.0))
        s, c = row["state"], make_schedule(row["state"])
        log(f"{s['node_id']:<9} soc={s['stored_energy_pct']:5.1f}% "
            f"-> {c['mode']:<8} every {c['sampling_interval_s']}s")
    log(f"replay done ({lines[-1]['t'] - t0:.0f}s of recorded time)")


# ---------------------------------------------------------------- selftest --
def run_selftest():
    """No broker, no Wokwi, no network. Proves the decision logic before M1 is
    ready, so the integration session is spent on wiring, not on debugging this."""
    cases = [(100.0, "HIGH"), (70.0, "HIGH"), (69.9, "MEDIUM"), (40.0, "MEDIUM"),
             (39.9, "LOW"), (15.0, "LOW"), (14.9, "CRITICAL"), (0.0, "CRITICAL")]
    for soc, want in cases:
        got, interval = decide_mode(soc)
        assert got == want, f"soc {soc} -> {got}, expected {want}"
        assert (interval == 0) == (got == "CRITICAL")
    print(f"  mode ladder: {len(cases)} boundary cases ok")

    state = {"node_id": "floor", "ts": 91234, "stored_energy_pct": 63.2,
             "mode": "MEDIUM", "sampling_interval_s": 20, "last_event": "motion",
             "sensor": {"temp_c": 24.1, "rh": 48.0}, "fw": "0.1"}
    assert validate_state(state) is None
    cmd = make_schedule(state)
    assert cmd["mode"] == "MEDIUM" and cmd["node_id"] == "floor"
    assert "alarm" in cmd["task_set"], "the alarm task must survive every mode"
    print("  make_schedule on the schema's own example ok")

    schema = json.loads(SCHEMA.read_text())
    for name, obj in (("NodeState", state), ("ScheduleCommand", cmd)):
        d = schema["definitions"][name]
        missing = [f for f in d["required"] if f not in obj]
        extra = [k for k in obj if k not in d["properties"]]
        assert not missing, f"{name} missing {missing}"
        assert not extra, f"{name} has fields not in the schema: {extra}"
        print(f"  {name}: required fields present, no undeclared fields")

    for bad, why in [({}, "empty"),
                     ({**state, "node_id": "kitchen"}, "unknown node"),
                     ({**state, "stored_energy_pct": 140}, "soc out of range")]:
        assert validate_state(bad) is not None, f"should have rejected: {why}"
    print("  validate_state rejects malformed messages")
    print("\nselftest passed -- decision logic is correct; only the broker link "
          "remains untested (needs Reet's node).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", default="ehsh-CHANGEME",
                    help="team-unique MQTT prefix; must match firmware/node.ino")
    ap.add_argument("--host", default="broker.hivemq.com")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--record", type=Path,
                    help="append every exchange to this jsonl for --replay")
    ap.add_argument("--replay", type=Path, help="re-run a recording, no broker")
    ap.add_argument("--speed", type=float, default=4.0, help="replay speed")
    ap.add_argument("--drive-energy", action="store_true",
                    help="hub supplies stored_energy_pct, overriding the node's "
                         "potentiometer (full closed-loop run)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        run_selftest()
    elif a.replay:
        run_replay(a.replay, a.speed)
    else:
        if a.prefix == "ehsh-CHANGEME":
            sys.exit("set --prefix to the string in firmware/node.ino "
                     "(the public broker is shared with the whole internet)")
        run_live(a.prefix, a.host, a.port, a.record, a.drive_energy)


if __name__ == "__main__":
    main()
