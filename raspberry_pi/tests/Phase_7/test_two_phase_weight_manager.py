#!/usr/bin/env python3
"""
Two-Phase Weight Manager Full Test Suite
==========================================

Runs four test cases in sequence against a LIVE M5StickC scale over MQTT.

Test 1 Baseline capture
Test 2 Correct pill removal  (remove exactly EXPECTED_PILLS)
Test 3 Wrong pill removal    (remove a different number)
Test 4 No pills removed      (lift bottle and replace without taking any)

IMPORTANT how each test works
--------------------------------
The script tells you what to do, then you press Enter.
The moment you press Enter, the script starts watching the scale.
You must do the physical action AFTER pressing Enter.

Usage
-----
    python -m raspberry_pi.tests.Phase_7.test_two_phase_weight_manager

Prerequisites
-------------
- M5StickC station_1 powered on and publishing weight data over MQTT
- config/config.yaml present and pointing at your MQTT broker
- Full pill bottle placed on the scale before starting
"""

import sys
import time
import yaml
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from raspberry_pi.services.mqtt_client import MQTTClient
from raspberry_pi.modules.weight_manager import WeightManager


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

STATION_ID     = "station_1"
EXPECTED_PILLS = 2
TOLERANCE      = 0

WAIT_FOR_DATA_TIMEOUT  = 20
WAIT_FOR_EVENT_TIMEOUT = 60

# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

def make_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("weight_test")


def separator(title: str = ""):
    width = 60
    if title:
        pad = max(1, (width - len(title) - 2) // 2)
        print("\n" + "-" * pad + f" {title} " + "-" * pad)
    else:
        print("\n" + "-" * width)


def wait_for_live_data(wm: WeightManager, station_id: str, timeout: int) -> bool:
    print(f"  Waiting up to {timeout}s for live data from {station_id}...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if wm.get_current_weight(station_id) is not None:
            return True
        time.sleep(0.3)
    return False


def wait_for_stable(wm: WeightManager, station_id: str, timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if wm.is_stable(station_id):
            return True
        time.sleep(0.3)
    return False


def wait_for_event(wm: WeightManager, station_id: str,
                   timeout: int, before_ts: float) -> dict | None:
    """
    Block until a removal event with timestamp > before_ts is recorded.

    before_ts MUST be captured before the user does the physical action
    (i.e. right before the Enter prompt) so that events fired during the
    wait window are never missed.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        event = wm.last_event_data.get(station_id)
        if event and event.get("timestamp", 0) > before_ts:
            return event
        time.sleep(0.2)
    return None


def print_status(wm: WeightManager, station_id: str):
    s = wm.get_station_status(station_id)
    print(
        f"  weight={s.get('weight_g')}g  "
        f"stable={s.get('stable')}  "
        f"baseline={s.get('baseline_g')}g  "
        f"phase={s.get('detection_phase')}"
    )


def print_event(event: dict | None):
    if not event:
        print("  (no event recorded)")
        return
    print(f"  event_type    : {event.get('event_type')}")
    print(f"  pills_removed : {event.get('pills_removed')}")
    print(f"  delta_g       : {event.get('delta_g')} g")
    print(f"  baseline_g    : {event.get('previous_baseline_g')} g")
    print(f"  new_weight_g  : {event.get('current_weight_g')} g")

def check(label: str, condition: bool) -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}]  {label}")
    return condition


def prompt_action(steps: list[str]):
    """Print numbered steps, then block until Enter is pressed."""
    print()
    for i, step in enumerate(steps, 1):
        print(f"    {i}. {step}")
    print()
    print("  >>> Press Enter and then IMMEDIATELY do the steps above <<<")
    input("  > ")


# -----------------------------------------------------------------------------
# TEST CASES
# -----------------------------------------------------------------------------

def test_baseline_capture(wm: WeightManager) -> bool:
    separator("TEST 1 - Baseline Capture")

    print()
    print("  Make sure the FULL pill bottle is sitting still on the scale.")
    input("  Press Enter when it is stable... ")

    print("  Waiting for a stable reading...")
    if not wait_for_stable(wm, STATION_ID, timeout=15):
        print("  ERROR: scale did not stabilise within 15 s.")
        return False

    print_status(wm, STATION_ID)
    ok = wm.capture_current_baseline(STATION_ID)
    passed = check("Baseline captured successfully", ok)
    if ok:
        print(f"  Baseline = {wm.baseline_weights[STATION_ID]:.2f} g")
    return passed


def _recapture_baseline(wm: WeightManager, label: str) -> bool:
    """Ask the user to restore the full bottle, then re-capture baseline."""
    print()
    print(f"  PREP for {label}:")
    print("  Put ALL pills back into the bottle so it is at full weight.")
    print("  Leave the bottle on the scale and wait for it to stabilise.")
    input("  Press Enter once the bottle is stable at full weight... ")

    print("  Re-capturing baseline...")
    if not wait_for_stable(wm, STATION_ID, timeout=15):
        print("  ERROR: could not get a stable reading.")
        return False

    wm.capture_current_baseline(STATION_ID)
    print(f"  New baseline = {wm.baseline_weights[STATION_ID]:.2f} g")
    return True
    
def test_correct_removal(wm: WeightManager) -> bool:
    separator(f"TEST 2 - Correct Removal ({EXPECTED_PILLS} pill(s))")

    # Clear any stale event and record before_ts BEFORE enabling detection
    wm.last_event_data.pop(STATION_ID, None)
    before_ts = time.time()
    wm.enable_event_detection(STATION_ID)

    prompt_action([
        "Lift the bottle completely off the scale.",
        f"Remove exactly {EXPECTED_PILLS} pill(s).",
        "Place the bottle back and leave it still.",
    ])

    print(f"  Watching for up to {WAIT_FOR_EVENT_TIMEOUT}s...")
    event = wait_for_event(wm, STATION_ID, WAIT_FOR_EVENT_TIMEOUT, before_ts)

    print("\n  Result:")
    print_event(event)
    wm.disable_event_detection(STATION_ID)

    if not event:
        check("Removal event received", False)
        return False

    verify = wm.verify_dosage(STATION_ID, EXPECTED_PILLS, tolerance=TOLERANCE)
    print(f"\n  Dosage check: {verify}")

    return all([
        check("Removal event received",               True),
        check("Correct pill count detected",          verify["verified"]),
        check(f"pills_removed == {EXPECTED_PILLS}",   verify["actual"] == EXPECTED_PILLS),
    ])


def test_wrong_removal(wm: WeightManager) -> bool:
    wrong = 1 if EXPECTED_PILLS != 1 else 3
    separator(f"TEST 3 - Wrong Removal ({wrong} pill(s), expected {EXPECTED_PILLS})")

    if not _recapture_baseline(wm, "Test 3"):
        return False

    wm.last_event_data.pop(STATION_ID, None)
    before_ts = time.time()
    wm.enable_event_detection(STATION_ID)

    prompt_action([
        "Lift the bottle completely off the scale.",
        f"Remove exactly {wrong} pill(s)  <-- deliberately wrong amount.",
        "Place the bottle back and leave it still.",
    ])

    print(f"  Watching for up to {WAIT_FOR_EVENT_TIMEOUT}s...")
    event = wait_for_event(wm, STATION_ID, WAIT_FOR_EVENT_TIMEOUT, before_ts)

    print("\n  Result:")
    print_event(event)
    wm.disable_event_detection(STATION_ID)

    if not event:
        check("Removal event received", False)
        return False

    verify = wm.verify_dosage(STATION_ID, EXPECTED_PILLS, tolerance=TOLERANCE)
    print(f"\n  Dosage check: {verify}")

    return all([
        check("Removal event received",                   True),
        check("Dosage correctly flagged as INCORRECT",    not verify["verified"]),
        check(f"pills_removed == {wrong}",                verify["actual"] == wrong),
    ])
    
def test_no_pills_removed(wm: WeightManager) -> bool:
    separator("TEST 4 - No Pills Removed (lift and replace)")

    if not _recapture_baseline(wm, "Test 4"):
        return False

    wm.last_event_data.pop(STATION_ID, None)
    before_ts = time.time()
    wm.enable_event_detection(STATION_ID)

    prompt_action([
        "Lift the bottle completely off the scale.",
        "Do NOT remove any pills.",
        "Place the bottle back in exactly the same position and leave it still.",
    ])

    print(f"  Watching for up to {WAIT_FOR_EVENT_TIMEOUT}s...")
    event = wait_for_event(wm, STATION_ID, WAIT_FOR_EVENT_TIMEOUT, before_ts)

    print("\n  Result:")
    print_event(event)
    wm.disable_event_detection(STATION_ID)

    if event is None:
        return check("No removal event fired (noise filter working correctly)", True)

    pills = event.get("pills_removed", 0)
    delta = event.get("delta_g", 0.0)
    return check(
        f"pills_removed=0 even though event fired (delta={delta:.2f}g is noise)",
        pills == 0,
    )


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

def main():
    logger = make_logger()

    separator("Two-Phase Weight Manager - Full Test Suite")
    print(f"  Station  : {STATION_ID}")
    print(f"  Expected : {EXPECTED_PILLS} pill(s)  |  tolerance={TOLERANCE}")
    print()
    print("  KEY RULE: For every test, read the steps shown on screen,")
    print("  then press Enter. The script starts watching the scale the")
    print("  MOMENT you press Enter -- so act immediately after pressing it.")

    config_path = Path("config/config.yaml")
    if not config_path.exists():
        print(f"\nERROR: {config_path} not found. Run from the project root.")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    events_log: list[dict] = []

    def on_removal(event):
        events_log.append(event)
        logger.info(f"[CALLBACK] {event}")

    wm   = WeightManager(config["weight_sensors"], logger)
    wm.set_pill_removal_callback(on_removal)

    mqtt = MQTTClient(config["mqtt"], logger)
    mqtt.set_weight_callback(wm.process_weight_data)
    mqtt.connect()
    
    separator("Connecting to scale")
    if not wait_for_live_data(wm, STATION_ID, WAIT_FOR_DATA_TIMEOUT):
        print(f"\nERROR: no data from {STATION_ID} within {WAIT_FOR_DATA_TIMEOUT}s.")
        mqtt.disconnect()
        sys.exit(1)

    print("  Live data received.")
    print_status(wm, STATION_ID)

    results: dict[str, bool] = {}
    try:
        results["T1 Baseline Capture"] = test_baseline_capture(wm)
        results["T2 Correct Removal"]  = test_correct_removal(wm)
        results["T3 Wrong Removal"]    = test_wrong_removal(wm)
        results["T4 No Pills Removed"] = test_no_pills_removed(wm)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    finally:
        mqtt.disconnect()

    separator("SUMMARY")
    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}]  {name}")
        if not passed:
            all_passed = False

    print(f"\n  Total callbacks fired during session: {len(events_log)}")
    separator()

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
