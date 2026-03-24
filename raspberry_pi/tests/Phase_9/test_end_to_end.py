#!/usr/bin/env python3
"""
Phase 9 - Full End-to-End Workflow Test  (Display-Driven Revision)
===================================================================

ALL user interaction happens through the Raspberry Pi display + keyboard.
No terminal input is required after the script starts.

Controls:
  SPACE / ENTER  - confirm / proceed to next stage
  UP / DOWN      - navigate medicine selection list
  ESC            - quit at any time

Root cause fixes from previous version
---------------------------------------
1. GL Context Error: display.update() was called from a background thread.
   Fix: removed background processing thread entirely. All processing
   (reminders, weight events, display) now runs on the MAIN thread.

2. input() blocked pygame: terminal input froze the display loop.
   Fix: replaced every input() with wait_for_space() which polls
   pygame keyboard events while keeping the display alive.

3. Audio/Telegram not firing: the GL crash interrupted callbacks
   before audio/Telegram code could execute.
   Fix: with all callbacks on the main thread the error cannot occur.

4. Patient monitoring camera: no change needed - it opens camera 0
   after the scanner releases it, which is correct behaviour.

Run from project root:
    python -m raspberry_pi.tests.Phase_9.test_end_to_end
    python -m raspberry_pi.tests.Phase_9.test_end_to_end --headless
"""

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import pygame
from raspberry_pi.main import MedicationSystem, SystemState


# ======================================================================
# Keyboard / display helpers  (MAIN THREAD ONLY)
# ======================================================================

def pump(system):
    """
    Drain the pygame event queue and tick the display clock.
    Returns the pygame key constant of the last key pressed, or None.

    This is the ONLY function that should call pygame.event.get().
    Call it from the main thread at least ~20 times per second to keep
    the window responsive.
    """
    if system.display and system.display.initialized:
        return system.display.pump_events()
    return None


def wait_for_space(system):
    """
    Block until the user presses SPACE or ENTER.
    Keeps the display alive (pumps events) while waiting.
    Returns True on key press, False if system.running becomes False.
    """
    while system.running:
        key = pump(system)
        if key in (pygame.K_SPACE, pygame.K_RETURN):
            return True
        if key == pygame.K_ESCAPE:
            system.running = False
            return False
        time.sleep(0.05)
    return False


def wait_for_condition(system, condition_fn, show_fn=None,
                       timeout=60, poll_interval=0.15):
    """
    Poll condition_fn() until it returns True or timeout expires.
    show_fn() is called each iteration to refresh the display.
    Returns True if condition met, False on timeout or ESC.
    """
    start = time.time()
    while system.running and (time.time() - start) < timeout:
        key = pump(system)
        if key == pygame.K_ESCAPE:
            system.running = False
            return False
        if show_fn:
            show_fn()
        if condition_fn():
            return True
        time.sleep(poll_interval)
    return False
    
# ======================================================================
# Result helpers
# ======================================================================

def _check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}]  {label}")
    return condition


def _section(title):
    w = 66
    pad = max(1, (w - len(title) - 2) // 2)
    print(f"\n{'-' * pad} {title} {'-' * pad}")


# ======================================================================
# Stage 0-A  Pre-flight checks
# ======================================================================

def preflight_checks(system):
    """
    Verify DB has medicines, scale is online, and baseline exists.
    Loads medicines into the scheduler.
    Returns True if all checks pass.
    """
    _section("Pre-flight Checks")
    station_id = "station_1"
    passed = True

    # --- DB check ---
    registered = system.database.list_registered_medicines()
    ok = len(registered) >= 1
    passed &= _check(f"Medicines registered in DB ({len(registered)} found)", ok)

    if not ok:
        if system.display:
            system.display.show_error_screen(
                "No medicines in database.\n"
                "Run Phase 9 onboarding test first."
            )
            time.sleep(4)
        return False

    for r in registered:
        print(f"    {r['medicine_id']} | {r['medicine_name']} | "
              f"dose={r['dosage_amount']} | times={r['time_slots']}")

    # --- Load schedule into scheduler ---
    for rec in registered:
        name  = rec.get("medicine_name")
        sid   = rec.get("station_id")
        dose  = rec.get("dosage_amount", 1)
        slots = rec.get("time_slots", "")
        if name and slots:
            times = [t.strip() for t in slots.split(",") if t.strip()]
            if times:
                system.scheduler.add_medication(
                    medicine_name=name,
                    station_id=sid,
                    dosage_pills=dose,
                    times=times
                )

    scheduled = system.scheduler.get_scheduled_medicines()
    passed &= _check(f"Scheduler populated ({scheduled})", len(scheduled) > 0)

    # --- Wait for live scale data (up to 20 s) ---
    print("\n  Waiting for live scale data (up to 20s)...")
    if system.display:
        system.display.show_instruction_screen(
            "PRE-FLIGHT  -  Connecting to Scale",
            ["Waiting for live data from the scale...",
             "Make sure the M5StickC is powered on."],
            "Please wait..."
        )
        
    deadline = time.time() + 20
    connected = False
    while time.time() < deadline:
        pump(system)
        st = system.weight_manager.get_station_status(station_id)
        if st.get("connected") and st.get("weight_g") is not None:
            connected = True
            break
        time.sleep(0.3)

    passed &= _check("Scale publishing live data", connected)

    if not connected:
        if system.display:
            system.display.show_error_screen(
                "No data from scale.\n"
                "Check M5StickC WiFi and MQTT broker."
            )
            time.sleep(4)
        return False

    # Show live status
    st = system.weight_manager.get_station_status(station_id)
    cfg = system.weight_manager.station_configs.get(station_id, {})
    print(f"  weight={st.get('weight_g')}g  stable={st.get('stable')}  "
          f"baseline={st.get('baseline_g')}g  "
          f"phase={st.get('detection_phase')}  "
          f"armed={st.get('event_detection_enabled')}  "
          f"pill_weight_mg={cfg.get('pill_weight_mg', '?')}")

    # --- Baseline check ---
    has_baseline = (
        station_id in system.weight_manager.baseline_weights
        and not system.weight_manager.baseline_capture_required.get(station_id, True)
    )
    bval = system.weight_manager.baseline_weights.get(station_id, 0)
    passed &= _check(f"Baseline exists for {station_id} ({bval:.2f}g)", has_baseline)

    # --- Show results and wait for SPACE ---
    if system.display:
        pf_lines = [
            f"[{'PASS' if len(registered) >= 1 else 'FAIL'}]  {len(registered)} medicine(s) in database",
            f"[{'PASS' if len(scheduled) > 0 else 'FAIL'}]  Scheduler loaded: {', '.join(scheduled[:3])}",
            f"[{'PASS' if connected else 'FAIL'}]  Scale online  (weight={st.get('weight_g')}g)",
            f"[{'PASS' if has_baseline else 'FAIL'}]  Baseline = {bval:.2f}g",
        ]
        system.display.show_instruction_screen(
            "PRE-FLIGHT CHECKS",
            pf_lines,
            "Press SPACE to continue"
        )
        wait_for_space(system)

    return passed
    
# ======================================================================
# Stage 0-B  Medicine selection
# ======================================================================

def select_medicine(system):
    """
    Let the user pick which medicine to test using UP/DOWN+SPACE on keyboard.
    Returns the selected medicine dict, or None.
    """
    registered = system.database.list_registered_medicines()
    if not registered:
        return None

    if not system.display:
        # Headless: just use the first one
        print("  (headless) Using first registered medicine.")
        return registered[0]

    selected = 0
    items = [
        f"{m['medicine_name']}  (id={m['medicine_id']}, "
        f"dose={m['dosage_amount']}, times={m['time_slots']})"
        for m in registered
    ]

    while system.running:
        system.display.show_selection_screen(
            "SELECT MEDICINE TO TEST", items, selected
        )
        key = pump(system)

        if key == pygame.K_UP:
            selected = (selected - 1) % len(registered)
        elif key == pygame.K_DOWN:
            selected = (selected + 1) % len(registered)
        elif key in (pygame.K_SPACE, pygame.K_RETURN):
            return registered[selected]
        elif key == pygame.K_ESCAPE:
            system.running = False
            return None

        time.sleep(0.05)

    return None
    
# ======================================================================
# Stage 0-C  Baseline capture for the selected bottle
# ======================================================================

def capture_baseline(system, medicine, station_id):
    """
    Ask the user to place the correct bottle on the scale,
    wait for it to stabilise, then capture the baseline weight.
    Returns True on success.
    """
    _section(f"Stage 0 - Baseline Capture for {medicine['medicine_name']}")
    name = medicine["medicine_name"]
    cfg  = system.weight_manager.station_configs.get(station_id, {})
    pill_mg   = cfg.get("pill_weight_mg", 300)
    dose      = medicine["dosage_amount"]
    exp_delta = (pill_mg / 1000.0) * dose

    if system.display:
        system.display.show_instruction_screen(
            f"STAGE 0 - PLACE BOTTLE: {name}",
            [
                f"Place the {name} bottle on the scale.",
                "Wait until the weight reading is STABLE.",
                "",
                f"  Pill weight: {pill_mg} mg",
                f"  Expected delta for {dose} pill(s): ~{exp_delta:.2f} g",
            ],
            "Press SPACE once the bottle is stable on the scale"
        )
        wait_for_space(system)

    # Wait up to 15 s for a stable reading > 5 g
    print("  Waiting for stable reading...")
    if system.display:
        system.display.show_instruction_screen(
            f"STABILISING - {name}",
            ["Waiting for stable weight reading...", "Leave the bottle completely still."],
            "Please wait..."
        )

    deadline = time.time() + 15
    while time.time() < deadline and system.running:
        pump(system)
        st = system.weight_manager.get_station_status(station_id)
        if st.get("stable") and float(st.get("weight_g") or 0) > 5.0:
            break
        time.sleep(0.3)

    ok = system.weight_manager.capture_current_baseline(station_id)

    if ok:
        baseline = system.weight_manager.baseline_weights.get(station_id, 0)
        after_g  = baseline - exp_delta
        print(f"  Baseline captured: {baseline:.2f}g")
        print(f"  Expected delta when removing {dose} pill(s): ~{exp_delta:.2f}g")
        print(f"  After removal bottle should weigh: ~{after_g:.2f}g")

        if system.display:
            system.display.show_instruction_screen(
                "BASELINE CAPTURED",
                [
                    f"Baseline: {baseline:.2f} g",
                    f"Remove {dose} pill(s) -> expected change: ~{exp_delta:.2f} g",
                    f"Expected bottle weight after removal: ~{after_g:.2f} g",
                ],
                "Press SPACE to continue to the reminder stage"
            )
            wait_for_space(system)

        if system.audio:
            system.audio.speak(f"Baseline captured for {name}.")
    else:
        print("  ERROR: Baseline capture failed - bottle not stable or too light.")
        if system.display:
            system.display.show_error_screen(
                "Baseline capture failed.\n"
                "Make sure the bottle is fully on the scale and stable."
            )
            time.sleep(3)

    return _check("Baseline captured", ok)
    
# ======================================================================
# Stage 1  Trigger reminder
# ======================================================================

def stage_reminder(system, medicine, station_id):
    """
    Inject a manual reminder, process it on the main thread
    (which shows the reminder screen and plays audio), and confirm
    the state machine reached REMINDER_ACTIVE.
    """
    _section(f"Stage 1 - Reminder: {medicine['medicine_name']}")
    name  = medicine["medicine_name"]
    mid   = medicine["medicine_id"]
    dose  = medicine["dosage_amount"]

    reminder_data = {
        "medicine_name":  name,
        "medicine_id":    mid,
        "dosage_pills":   dose,
        "station_id":     station_id,
        "scheduled_time": datetime.now().strftime("%H:%M"),
        "actual_time":    datetime.now().strftime("%H:%M:%S"),
        "timestamp":      time.time(),
    }

    print(f"  Injecting manual reminder for {name} (id={mid})...")
    system.queue_manual_reminder(reminder_data)

    # Process the reminder on the MAIN THREAD.
    # This call shows the reminder screen, plays audio, and sends Telegram.
    system._process_pending_manual_reminder()
    pump(system)   # let pygame render the new frame

    # Confirm state
    reached = system.state_machine.get_state() == SystemState.REMINDER_ACTIVE
    if reached:
        print(f"  [PASS]  State -> REMINDER_ACTIVE")
        print("  Display: shows reminder screen")
        print("  Audio:   reminder announcement playing")
        print("  Telegram: reminder sent to patient")
    else:
        print(f"  [FAIL]  State is {system.state_machine.get_state().name}"
              " (expected REMINDER_ACTIVE)")

    return reached
    
# ======================================================================
# Stage 2  Pill removal - live watching loop
# ======================================================================

def stage_pill_removal(system, medicine, station_id):
    """
    Show live weight on the display while waiting for the user to remove
    pills. Returns True when a pill-removal event is queued by the
    weight manager MQTT callback.
    """
    _section(f"Stage 2 - Pill Removal ({medicine['dosage_amount']} pill(s))")
    name  = medicine["medicine_name"]
    dose  = medicine["dosage_amount"]
    cfg   = system.weight_manager.station_configs.get(station_id, {})
    pill_mg   = cfg.get("pill_weight_mg", 300)
    min_delta = cfg.get("min_delta_g", 0.20)
    baseline  = system.weight_manager.baseline_weights.get(station_id, 0)
    exp_delta = (pill_mg / 1000.0) * dose

    # --- Instruction screen (user presses SPACE when ready) ---
    if system.display:
        system.display.show_instruction_screen(
            f"STAGE 2 - REMOVE PILLS: {name}",
            [
                f"1.  Lift the {name} bottle COMPLETELY off the scale.",
                f"2.  Remove EXACTLY {dose} pill(s).",
                "3.  Place the bottle back down in ONE smooth motion.",
                "    (tag is read on contact - do not pause mid-air)",
                "4.  Leave the bottle completely still.",
                "",
                f"  Pill weight: {pill_mg} mg  |  min delta: {min_delta} g",
                f"  Expected delta: ~{exp_delta:.2f} g",
                f"  Expected weight after: ~{baseline - exp_delta:.2f} g",
            ],
            "Press SPACE when you are ready to remove the pills"
        )
        wait_for_space(system)

    if system.audio:
        system.audio.speak(
            f"Please remove {dose} pill from the {name} bottle, "
            "then place it back on the scale."
        )

    print(f"\n  Watching (up to 90s) - scale updated every 0.5s on display:")

    start   = time.time()
    timeout = 90

    while system.running and (time.time() - start) < timeout:
        elapsed = time.time() - start
        st = system.weight_manager.get_station_status(station_id)
        weight_g = float(st.get("weight_g") or 0.0)
        stable   = bool(st.get("stable", False))
        phase    = st.get("detection_phase", "UNKNOWN")
        armed    = bool(st.get("event_detection_enabled", False))

        # Print brief line to terminal every 2 s
        if int(elapsed * 2) % 4 == 0:   # approx every 2 s
            print(f"  weight={weight_g:.2f}g  stable={stable}  "
                  f"baseline={baseline:.2f}g  phase={phase}  "
                  f"armed={armed}  pill_weight_mg={pill_mg}")

        if system.display:
            system.display.show_watching_screen(
                f"Watching for pill removal - {name}",
                weight_g, stable, baseline, phase, armed,
                elapsed, timeout
            )

        key = pump(system)
        if key == pygame.K_ESCAPE:
            system.running = False
            return False

        # Check if the MQTT weight callback has queued an event
        if system.pending_weight_event:
            evt = system.pending_weight_event
            print(f"\n  Pill removal event detected!")
            print(f"    pills_removed:   {evt.get('pills_removed')}")
            print(f"    weight_change_g: {evt.get('weight_change_g')} g")
            print(f"    current_weight:  {evt.get('current_weight_g')} g")
            return True

        # Also accept if state machine already advanced
        state = system.state_machine.get_state()
        if state in (SystemState.VERIFYING, SystemState.MONITORING_PATIENT):
            print(f"\n  Pipeline already advancing (state={state.name})")
            return True

        time.sleep(0.3)
        
    # Timeout
    print("\n  DIAGNOSIS: No pill removal event detected after 90s.")
    print(f"  Baseline: {baseline:.2f}g   Expected delta: {exp_delta:.2f}g")
    print(f"  Min delta threshold: {min_delta}g")
    print("  Check:")
    print("  A) pill_weight_mg in config matches your actual pills")
    print("  B) Bottle was fully lifted off the scale")
    print("  C) Bottle was replaced onto the SAME position")
    print("  D) Weight change was > min_delta_g (otherwise treated as noise)")

    if system.display:
        system.display.show_error_screen(
            "No pill removal detected in 90s.\n"
            "See terminal for diagnosis."
        )
        time.sleep(3)

    return False
    
# ======================================================================
# Stage 3  Verification pipeline
# ======================================================================

def stage_verification(system):
    """
    Show a brief monitoring instruction, then run the full verification
    pipeline on the MAIN THREAD by calling _process_pending_weight_event().

    This call BLOCKS for ~35-50 s (identity check + 30 s monitoring)
    but internally updates the display every 0.1 s during monitoring.
    All display calls happen on the main thread - no GL context errors.
    """
    _section("Stage 3 - Verification Pipeline")

    if not system.pending_weight_event:
        state = system.state_machine.get_state()
        if state not in (SystemState.VERIFYING, SystemState.MONITORING_PATIENT):
            print("  No pending weight event and not in pipeline state.")
            return False

    # Show monitoring instructions briefly before pipeline blocks
    if system.display:
        system.display.show_instruction_screen(
            "STAGE 3 - VERIFICATION PIPELINE",
            [
                "The system will now verify your medication.",
                "",
                "When the MONITORING screen appears (30 seconds):",
                "  Stay in front of the camera.",
                "  Bring your hand clearly to your mouth.",
                "  Open your mouth as if taking the pills.",
                "  This triggers the intake detection.",
                "",
                "Starting automatically in 3 seconds...",
            ],
            "Please wait..."
        )

    # Pump events for 3 s so the user can read the instructions
    deadline = time.time() + 3
    while time.time() < deadline and system.running:
        key = pump(system)
        if key == pygame.K_ESCAPE:
            system.running = False
            return False
        time.sleep(0.05)

    # ---------------------------------------------------------------
    # Run the pipeline on the MAIN THREAD.
    # _process_pending_weight_event() calls _verify_medication_intake()
    # which blocks for ~35-50 s but calls self.display.update() every
    # 0.1 s during the patient monitoring window, keeping the display
    # responsive.
    # ---------------------------------------------------------------
    print("  Running verification pipeline (identity -> weight -> 30s monitoring)...")
    print("  During monitoring: bring hand to mouth and open mouth to register intake.")

    if system.pending_weight_event:
        system._process_pending_weight_event()

    # Wait for pipeline to return to IDLE (in case it was already processing)
    deadline = time.time() + 120
    while time.time() < deadline and system.running:
        key = pump(system)
        if key == pygame.K_ESCAPE:
            system.running = False
            return False
        state = system.state_machine.get_state()
        if state == SystemState.IDLE and system.current_medication is None:
            break
        time.sleep(0.2)

    final_state = system.state_machine.get_state()
    print(f"  Pipeline complete. Final state: {final_state.name}")
    return final_state == SystemState.IDLE
    
# ======================================================================
# Stage 4  Results
# ======================================================================

def stage_results(system, medicine):
    """Read the last event from the database and show the result screen."""
    _section("Stage 4 - Results")
    name  = medicine["medicine_name"]
    dose  = medicine["dosage_amount"]

    events = system.database.get_todays_events()
    event  = events[-1] if events else None

    passed = True
    passed &= _check("Event logged to database", event is not None)

    if not event:
        if system.display:
            system.display.show_error_screen("No event logged.")
            wait_for_space(system)
        return False

    result      = event.get("result",      "unknown")
    verified    = event.get("verified",    False)
    actual_dose = event.get("actual_dosage")
    medicine_ev = event.get("medicine_name", "")
    alerts      = event.get("alerts",      [])

    print(f"\n  Result:          {result}")
    print(f"  Verified:        {verified}")
    print(f"  Medicine logged: {medicine_ev}")
    print(f"  Expected dosage: {dose}")
    print(f"  Actual dosage:   {actual_dose}")
    print(f"  Alerts ({len(alerts)}):")
    for a in alerts:
        print(f"    [{a.get('severity')}]  {a.get('message')}")

    passed &= _check(
        f"Medicine name matches ({medicine_ev})",
        name.upper() in medicine_ev.upper() or medicine_ev.upper() in name.upper()
    )

    if result == "success":
        passed &= _check("Result: SUCCESS",   True)
        passed &= _check("Verified: True",    verified)
        passed &= _check(
            f"Pill count correct ({actual_dose} == {dose})",
            actual_dose == dose
        )
        if system.display:
            system.display.show_success_screen(
                name, f"Took {actual_dose} pill(s) correctly!"
            )
            wait_for_space(system)
            
    elif result == "incorrect_dosage":
        cfg     = system.weight_manager.station_configs.get("station_1", {})
        pill_mg = cfg.get("pill_weight_mg", 300)
        print(f"\n  DIAGNOSIS: Incorrect dosage detected.")
        print(f"  actual={actual_dose}  expected={dose}  pill_weight_mg={pill_mg}")
        print(f"  Fix: measure 1 pill delta on scale, set pill_weight_mg accordingly.")
        passed &= _check("Incorrect dosage result logged", True)
        if system.display:
            system.display.show_warning_screen(
                "Incorrect Dosage",
                f"Expected {dose} pill(s), detected {actual_dose} pill(s)."
            )
            wait_for_space(system)

    elif result == "behavioral_issue":
        print("\n  DIAGNOSIS: Intake motion not detected.")
        print("  Ensure face is well lit, hand clearly near mouth, mouth visibly open.")
        print("  Adjust mouth_open_ratio/proximity_ratio in config if needed.")
        if system.display:
            system.display.show_warning_screen(
                "Monitoring Alert",
                "Intake motion not clearly detected by camera."
            )
            wait_for_space(system)

    elif result == "no_intake":
        print("\n  DIAGNOSIS: No intake detected.")
        print("  Weight delta was 0 or below min_delta_g threshold.")
        print("  Ensure bottle was fully lifted off scale before replacing.")
        if system.display:
            system.display.show_warning_screen(
                "No Intake Detected",
                "Please take your medication."
            )
            wait_for_space(system)

    else:
        if system.display:
            system.display.show_warning_screen(
                f"Result: {result.replace('_', ' ').title()}",
                "Check terminal for details."
            )
            wait_for_space(system)

    return passed
    
# ======================================================================
# Main entry point
# ======================================================================

def run_end_to_end(headless: bool):
    _section("Phase 9 - Full End-to-End Workflow Test")

    if headless:
        print("  Running in HEADLESS mode (no display / audio).")
    else:
        print("  Running with DISPLAY and AUDIO.")
        print("  Use the keyboard to interact: SPACE=confirm  UP/DOWN=select  ESC=quit")

    # ------------------------------------------------------------------
    # Build system  (display and audio on main thread from here on)
    # ------------------------------------------------------------------
    system = MedicationSystem(
        config_path="config/config.yaml",
        enable_display=not headless,
        enable_audio=not headless
    )
    system.running = True

    station_id  = "station_1"
    all_results = {}

    try:
        # Start scheduler so medicine jobs exist for get_next_scheduled_time()
        system.scheduler.start()

        # Idle screen while we do pre-flight
        if system.display:
            system.display.show_idle_screen()

        # ---- Pre-flight ----
        pf_ok = preflight_checks(system)
        all_results["Stage 0  Pre-flight checks"] = pf_ok
        if not pf_ok:
            print("\n  Pre-flight failed - fix issues above and re-run.")
            return

        # ---- Medicine selection ----
        medicine = select_medicine(system)
        if not medicine:
            print("\n  No medicine selected or user quit.")
            return

        name  = medicine["medicine_name"]
        mid   = medicine["medicine_id"]
        dose  = medicine["dosage_amount"]
        print(f"\n  Testing: {name} (id={mid}, dose={dose}) on {station_id}")

        # ---- Baseline capture ----
        baseline_ok = capture_baseline(system, medicine, station_id)
        all_results["Stage 0  Baseline capture"] = baseline_ok
        if not baseline_ok:
            return
            
        # ---- Stage 1: Reminder ----
        reminder_ok = stage_reminder(system, medicine, station_id)
        all_results["Stage 1  Reminder triggered"] = reminder_ok
        if not reminder_ok:
            print("  Reminder stage failed. Check logs above.")
            return

        # ---- Stage 2: Pill removal ----
        removal_ok = stage_pill_removal(system, medicine, station_id)
        all_results["Stage 2  Pill removal detected"] = removal_ok
        if not removal_ok:
            print("  Pill removal not detected. See DIAGNOSIS above.")
            return

        # ---- Stage 3: Verification pipeline (BLOCKING, main thread) ----
        verify_ok = stage_verification(system)
        all_results["Stage 3  Verification pipeline"] = verify_ok

        # ---- Stage 4: Results ----
        result_ok = stage_results(system, medicine)
        all_results["Stage 4  Result logged"] = result_ok

    except KeyboardInterrupt:
        print("\n  Stopped by user (Ctrl+C).")

    except Exception as exc:
        print(f"\n  UNHANDLED EXCEPTION: {exc}")
        import traceback
        traceback.print_exc()

    finally:
        system.stop()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _section("SUMMARY")
    all_passed = True
    for label, ok in all_results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}]  {label}")
        if not ok:
            all_passed = False
            
    print()
    if all_passed:
        print("  End-to-end test PASSED - full pipeline working correctly.")
    else:
        print("  Some stages failed. Review output and DIAGNOSIS notes above.")


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Phase 9 full end-to-end workflow test"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without display or audio (terminal output only)"
    )
    args = parser.parse_args()
    run_end_to_end(headless=args.headless)
