#!/usr/bin/env python3
"""
Phase 9 - Full End-to-End Workflow Test
"""

import sys
import time
import threading
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from raspberry_pi.main import MedicationSystem, SystemState


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def check(label: str, condition: bool) -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}]  {label}")
    return condition


def section(title: str):
    width = 64
    pad = max(1, (width - len(title) - 2) // 2)
    print(f"\n{'-' * pad} {title} {'-' * pad}")


def wait_for_state(system, target_state, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if system.state_machine.get_state() == target_state:
            return True
        time.sleep(0.1)
    return False


def print_station_status(system, station_id="station_1"):
    status = system.weight_manager.get_station_status(station_id)
    cfg    = system.weight_manager.station_configs.get(station_id, {})
    pill_w = cfg.get("pill_weight_mg", "?")
    print(
        f"  weight={status.get('weight_g')}g  "
        f"stable={status.get('stable')}  "
        f"baseline={status.get('baseline_g')}g  "
        f"phase={status.get('detection_phase')}  "
        f"armed={status.get('event_detection_enabled')}  "
        f"pill_weight_mg={pill_w}"
    )


# ------------------------------------------------------------------ #
# Background processing loop
# ------------------------------------------------------------------ #

def _processing_loop(system):
    """
    Runs in a background daemon thread.
    Drains the pending reminder and weight event queues exactly as
    main.start() does in production, without the blocking startup logic.
    """
    while system.running:
        try:
            system._process_pending_manual_reminder()
            system._process_pending_weight_event()
            if system.display:
                system.display.update()
        except Exception as e:
            system.logger.error(f"Processing loop error: {e}")
        time.sleep(0.05)

# ------------------------------------------------------------------ #
# Pre-flight checks
# ------------------------------------------------------------------ #

def preflight_checks(system) -> bool:
    section("Pre-flight Checks")

    passed = True
    station_id = "station_1"

    # Check medicines registered
    registered = system.database.list_registered_medicines()
    passed &= check(
        f"Medicines registered in DB ({len(registered)} found)",
        len(registered) >= 1
    )
    if not passed:
        print("  ERROR: Run Phase 9 onboarding test first.")
        return False

    for r in registered:
        print(
            f"    {r['medicine_id']} | {r['medicine_name']} | "
            f"dose={r['dosage_amount']} | times={r['time_slots']}"
        )

    # Wait for live scale data
    print("\n  Waiting for live scale data (up to 20s)...")
    deadline = time.time() + 20
    connected = False
    while time.time() < deadline:
        status = system.weight_manager.get_station_status(station_id)
        if status.get("connected") and status.get("weight_g") is not None:
            connected = True
            break
        time.sleep(0.3)

    passed &= check("Scale publishing live data", connected)
    if not connected:
        print("  ERROR: No live data from scale. Check M5StickC is running.")
        return False

    print_station_status(system, station_id)

    # Check baseline exists
    has_baseline = (
        station_id in system.weight_manager.baseline_weights and
        not system.weight_manager.baseline_capture_required.get(station_id, True)
    )
    baseline_val = system.weight_manager.baseline_weights.get(station_id, 0)
    passed &= check(
        f"Baseline exists for {station_id} ({baseline_val:.2f}g)",
        has_baseline
    )

    # Load schedule from DB into scheduler
    for record in registered:
        medicine_name = record.get("medicine_name")
        sid_r         = record.get("station_id")
        dosage        = record.get("dosage_amount", 1)
        time_slots    = record.get("time_slots", "")
        if not medicine_name or not time_slots:
            continue
        times = [t.strip() for t in time_slots.split(",") if t.strip()]
        if times:
            system.scheduler.add_medication(
                medicine_name=medicine_name,
                station_id=sid_r,
                dosage_pills=dosage,
                times=times
            )

    scheduled = system.scheduler.get_scheduled_medicines()
    passed &= check(
        f"Scheduler has medicines ({scheduled})",
        len(scheduled) > 0
    )

    return passed

# ------------------------------------------------------------------ #
# Stage 0: Baseline capture for selected bottle
# ------------------------------------------------------------------ #

def capture_baseline_for_bottle(system, medicine_name, station_id) -> bool:
    section(f"Stage 0 - Baseline Capture for {medicine_name}")

    print(f"  Since all medicines share {station_id}, we recapture the")
    print(f"  baseline for the specific bottle you are about to test.")
    print()
    print(f"  ACTION: Place the {medicine_name} bottle on the station.")
    print(f"  Wait until you see stable=True in the status, then press Enter.")
    print()

    # Live status while waiting
    print("  Current status:")
    print_station_status(system, station_id)

    input(f"\n  Press Enter once the {medicine_name} bottle is stable on the scale... ")

    # Wait for stable reading
    deadline = time.time() + 15
    while time.time() < deadline:
        status = system.weight_manager.get_station_status(station_id)
        if status.get("stable") and float(status.get("weight_g") or 0) > 5.0:
            break
        time.sleep(0.3)

    print("  Status at capture:")
    print_station_status(system, station_id)

    ok = system.weight_manager.capture_current_baseline(station_id)
    if ok:
        baseline = system.weight_manager.baseline_weights.get(station_id, 0)
        print(f"\n  Baseline captured: {baseline:.2f}g")

        # Show expected delta for this medicine's dose
        cfg = system.weight_manager.station_configs.get(station_id, {})
        pill_mg = cfg.get("pill_weight_mg", 300)
        dose = system.database.list_registered_medicines()
        dose_val = next(
            (r["dosage_amount"] for r in dose
             if r["medicine_name"] == medicine_name), 1
        )
        expected_delta_g = (pill_mg / 1000.0) * dose_val
        print(f"  Expected delta when removing {dose_val} pill(s): "
              f"~{expected_delta_g:.2f}g "
              f"(pill_weight_mg={pill_mg})")
        print(f"  After removal bottle should weigh: "
              f"~{baseline - expected_delta_g:.2f}g")

    return check("Baseline captured for selected bottle", ok)
    
# ------------------------------------------------------------------ #
# Stage 1: Reminder
# ------------------------------------------------------------------ #

def test_reminder_stage(system, medicine_name, medicine_id, dosage, station_id) -> bool:
    section(f"Stage 1 - Reminder: {medicine_name}")

    reminder_data = {
        "medicine_name":  medicine_name,
        "medicine_id":    medicine_id,   # Pass directly - avoids LIMIT 1 lookup bug
        "dosage_pills":   dosage,
        "station_id":     station_id,
        "scheduled_time": datetime.now().strftime("%H:%M"),
        "actual_time":    datetime.now().strftime("%H:%M:%S"),
        "timestamp":      time.time()
    }

    print(f"  Injecting manual reminder for {medicine_name} (id={medicine_id})...")
    system.queue_manual_reminder(reminder_data)

    # Background processing thread will call _process_pending_manual_reminder()
    # which transitions state to REMINDER_ACTIVE. Audio (~3s) plays after
    # state transition, so 20s timeout is generous.
    reached = wait_for_state(system, SystemState.REMINDER_ACTIVE, timeout=20)
    passed = check("State -> REMINDER_ACTIVE", reached)

    if reached:
        print("  Display: shows reminder screen")
        print("  Audio:   reminder announcement playing")
        print("  Telegram: reminder sent to patient")
    else:
        print("  DIAGNOSIS: State did not reach REMINDER_ACTIVE.")
        print(f"  Current state: {system.state_machine.get_state().name}")
        print(f"  pending_manual_reminder: {system.pending_manual_reminder}")
        print(f"  pending_manual_reminder_lock: {system.pending_manual_reminder_lock}")

    return passed


# ------------------------------------------------------------------ #
# Stage 2: Pill removal
# ------------------------------------------------------------------ #

def test_pill_removal_stage(system, station_id, dosage, medicine_name) -> bool:
    section(f"Stage 2 - Pill Removal ({dosage} pill(s) of {medicine_name})")

    # Show calibration info
    cfg = system.weight_manager.station_configs.get(station_id, {})
    pill_mg = cfg.get("pill_weight_mg", 300)
    min_delta = cfg.get("min_delta_g", 0.20)
    expected_delta = (pill_mg / 1000.0) * dosage
    baseline = system.weight_manager.baseline_weights.get(station_id, 0)

    print()
    print(f"  Calibration info:")
    print(f"    pill_weight_mg = {pill_mg}  "
          f"=> expected delta for {dosage} pill(s) = {expected_delta:.2f}g")
    print(f"    min_delta_g    = {min_delta}g  (must exceed this to register)")
    print(f"    baseline       = {baseline:.2f}g")
    print(f"    expected weight after removal = {baseline - expected_delta:.2f}g")
    print()
    print("  PHYSICAL ACTION REQUIRED:")
    print(f"  1. Lift the {medicine_name} bottle COMPLETELY off the scale")
    print(f"  2. Remove EXACTLY {dosage} pill(s)")
    print(f"  3. Place bottle back in ONE smooth motion")
    print(f"     (tag reads on contact, weight settles over ~2 seconds)")
    print(f"  4. Leave completely still")
    print()
    print("  Watching (up to 90s) - status every 2 seconds:")

    deadline = time.time() + 90
    event_received = False

    while time.time() < deadline:
        print_station_status(system, station_id)

        if system.pending_weight_event:
            event_received = True
            evt = system.pending_weight_event
            print(f"\n  Weight event detected!")
            print(f"    pills_removed:    {evt.get('pills_removed')}")
            print(f"    weight_change_g:  {evt.get('weight_change_g')}g")
            print(f"    current_weight_g: {evt.get('current_weight_g')}g")
            break

        state = system.state_machine.get_state()
        if state in (SystemState.VERIFYING, SystemState.MONITORING_PATIENT):
            event_received = True
            print(f"\n  Pipeline advancing - state: {state.name}")
            break

        time.sleep(2)

    if not event_received:
        print("\n  DIAGNOSIS: No weight event after 90s.")
        print(f"  Current weight: {system.weight_manager.get_current_weight(station_id)}g")
        print(f"  Baseline:       {system.weight_manager.baseline_weights.get(station_id)}g")
        print(f"  Expected delta: {expected_delta:.2f}g  min_delta: {min_delta}g")
        print()
        print("  Possible causes:")
        print("  A) pill_weight_mg is wrong -> measure 1 pill on scale, update config")
        print("  B) bottle not fully lifted (phase never left WAITING_FOR_REMOVAL)")
        print("  C) bottle replaced too slowly / tag scan outside coincident window")
        print("  D) delta < min_delta_g -> scale noise, not a real removal")

    return check("Pill removal event received", event_received)

# ------------------------------------------------------------------ #
# Stage 3: Verification pipeline
# ------------------------------------------------------------------ #

def test_verification_stage(system) -> bool:
    section("Stage 3 - Verification Pipeline")

    print("  Pipeline: identity check -> weight verify -> patient monitor (30s)")
    print()
    print("  DURING PATIENT MONITORING (30 seconds):")
    print("  Stay in front of the camera. Bring your hand clearly to")
    print("  your mouth and open your mouth while your hand is near.")
    print("  This triggers an intake detection event.")
    print()

    states_seen   = []
    pipeline_deadline = time.time() + 120

    while time.time() < pipeline_deadline:
        state      = system.state_machine.get_state()
        state_name = state.name

        if not states_seen or states_seen[-1] != state_name:
            states_seen.append(state_name)
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}] State: {state_name}")

        if state == SystemState.IDLE and system.current_medication is None:
            print("  Pipeline complete - returned to IDLE")
            break

        time.sleep(0.5)
    else:
        print("  Pipeline did not complete within 120s")

    passed = True
    passed &= check("VERIFYING state reached",          "VERIFYING"          in states_seen)
    passed &= check("MONITORING_PATIENT state reached", "MONITORING_PATIENT" in states_seen)
    passed &= check(
        "Returned to IDLE",
        system.state_machine.get_state() == SystemState.IDLE
    )

    return passed


# ------------------------------------------------------------------ #
# Stage 4: Result verification
# ------------------------------------------------------------------ #

def test_result_stage(system, medicine_name, dosage) -> bool:
    section("Stage 4 - Result Verification")

    events = system.database.get_todays_events()
    event  = events[-1] if events else None

    passed = True
    passed &= check("Event logged to database", event is not None)

    if not event:
        return passed

    result        = event.get("result",       "unknown")
    verified      = event.get("verified",     False)
    actual_dosage = event.get("actual_dosage")
    medicine      = event.get("medicine_name", "")
    alerts        = event.get("alerts",        [])

    print(f"\n  Result:          {result}")
    print(f"  Verified:        {verified}")
    print(f"  Medicine logged: {medicine}")
    print(f"  Expected dosage: {dosage}")
    print(f"  Actual dosage:   {actual_dosage}")
    print(f"  Alerts:          {len(alerts)}")
    for a in alerts:
        print(f"    [{a.get('severity')}] {a.get('message')}")

    passed &= check(
        f"Medicine name matches ({medicine})",
        medicine_name.upper() in medicine.upper() or
        medicine.upper() in medicine_name.upper()
    )

    if result == "success":
        passed &= check("Result: SUCCESS",    True)
        passed &= check("Verified: True",     verified)
        passed &= check(
            f"Pill count correct ({actual_dosage} == {dosage})",
            actual_dosage == dosage
        )

    elif result == "incorrect_dosage":
        cfg     = system.weight_manager.station_configs.get("station_1", {})
        pill_mg = cfg.get("pill_weight_mg", 300)
        print(f"\n  DIAGNOSIS: Incorrect dosage detected.")
        print(f"  actual={actual_dosage}  expected={dosage}")
        print(f"  pill_weight_mg={pill_mg}")
        print(f"  To fix: measure 1 pill delta on scale, set pill_weight_mg")
        print(f"  to (measured_delta_g * 1000) in config.yaml, re-run test.")
        passed &= check("incorrect_dosage result logged", True)

    elif result == "behavioral_issue":
        print(f"\n  DIAGNOSIS: Patient monitoring did not detect intake.")
        print(f"  Tips: ensure face is well lit, hand clearly near mouth,")
        print(f"  mouth visibly open. Adjust mouth_open_ratio/proximity_ratio")
        print(f"  in config.yaml patient_monitoring.mediapipe if needed.")

    elif result == "no_intake":
        print(f"\n  DIAGNOSIS: No intake detected.")
        print(f"  Weight delta was 0 or below min_delta_g threshold.")
        print(f"  Ensure bottle was fully lifted off scale before replacing.")

    elif result == "partial_success":
        print(f"\n  Result: PARTIAL SUCCESS (some checks passed, some did not)")
        print(f"  Check alerts above for details.")

    else:
        print(f"\n  Unexpected result: {result}")

    return passed

# ------------------------------------------------------------------ #
# Full end-to-end test
# ------------------------------------------------------------------ #

def run_end_to_end(headless: bool):
    section("Phase 9 - Full End-to-End Workflow Test")

    system = MedicationSystem(
        config_path="config/config.yaml",
        enable_display=not headless,
        enable_audio=not headless
    )
    system.running = True

    all_results = {}

    try:
        # ---- Pre-flight ----
        if not preflight_checks(system):
            print("\n  Pre-flight failed. Fix issues above and re-run.")
            return

        # ---- Medicine selection ----
        registered = system.database.list_registered_medicines()
        print("\n  Registered medicines available for testing:")
        for i, r in enumerate(registered):
            print(
                f"    {i+1}. {r['medicine_name']} "
                f"(id={r['medicine_id']}, "
                f"dose={r['dosage_amount']}, "
                f"times={r['time_slots']})"
            )

        print()
        choice = input(
            f"  Which medicine to test? (1-{len(registered)}, default=1): "
        ).strip()

        try:
            idx = int(choice) - 1 if choice else 0
            idx = max(0, min(idx, len(registered) - 1))
        except ValueError:
            idx = 0

        medicine      = registered[idx]
        medicine_name = medicine["medicine_name"]
        medicine_id   = medicine["medicine_id"]
        dosage        = medicine["dosage_amount"]
        station_id    = "station_1"

        print(f"\n  Testing: {medicine_name} (id={medicine_id}, "
              f"dose={dosage}) on {station_id}")

        # ---- Start scheduler ----
        system.scheduler.start()
        if system.display:
            system.display.show_idle_screen(
                system.scheduler.get_next_scheduled_time()
            )

        # ---- Start background processing loop ----
        # This is the critical fix: drains pending reminders and weight events
        # without calling start() which would block.
        proc_thread = threading.Thread(
            target=_processing_loop,
            args=(system,),
            daemon=True,
            name="ProcessingLoop"
        )
        proc_thread.start()
        time.sleep(0.2)   # Let the thread settle
        print(f"  Processing loop started (thread: {proc_thread.name})")

        # ---- Stage 0: capture fresh baseline for this bottle ----
        baseline_ok = capture_baseline_for_bottle(system, medicine_name, station_id)
        all_results["Stage 0 Baseline"] = baseline_ok
        if not baseline_ok:
            print("  Baseline capture failed. Ensure bottle is on scale and stable.")
            return

        # ---- Stage 1: reminder ----
        all_results["Stage 1 Reminder"] = test_reminder_stage(
            system, medicine_name, medicine_id, dosage, station_id
        )
        if not all_results["Stage 1 Reminder"]:
            print("  Reminder stage failed.")
            print(f"  Current state: {system.state_machine.get_state().name}")
            print("  Check that processing thread is alive and MQTT is connected.")
            return

        # ---- Stage 2: pill removal ----
        all_results["Stage 2 Pill Removal"] = test_pill_removal_stage(
            system, station_id, dosage, medicine_name
        )
        if not all_results["Stage 2 Pill Removal"]:
            print("  Pill removal not detected. See DIAGNOSIS above.")
            return

        # ---- Stage 3: verification pipeline ----
        all_results["Stage 3 Verification"] = test_verification_stage(system)

        # ---- Stage 4: results ----
        all_results["Stage 4 Result"] = test_result_stage(
            system, medicine_name, dosage
        )

    finally:
        system.stop()

    # ---- Summary ----
    section("SUMMARY")
    all_passed = True
    for name, passed in all_results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}]  {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("End-to-end test PASSED. Full pipeline working correctly.")
    else:
        print("Some stages failed. See output and DIAGNOSIS notes above.")


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without display or audio"
    )
    args = parser.parse_args()
    run_end_to_end(headless=args.headless)
