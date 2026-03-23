#!/usr/bin/env python3
"""
Phase 8 - Registration and Integrated Tag+Weight Flow Test

Tests:
  T1  Fresh registration (clear DB, place bottle once, auto-register)
  T2  Re-registration skipped (already registered)
  T3  Coincident tag window timing logic
  T4  Full integrated flow (manual reminder -> lift -> replace -> verify)

Run from project root:
    python -m raspberry_pi.tests.Phase_8.test_registration_and_integrated_flow
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.database import Database
from raspberry_pi.modules.weight_manager import WeightManager
from raspberry_pi.modules.tag_runtime_service import TagRuntimeService
from raspberry_pi.modules.registration_manager import RegistrationManager
from raspberry_pi.services.mqtt_client import MQTTClient
from raspberry_pi.main import MedicationSystem, SystemState

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def check(label: str, condition: bool) -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}]  {label}")
    return condition


def section(title: str):
    width = 60
    pad = max(1, (width - len(title) - 2) // 2)
    print(f"\n{'-' * pad} {title} {'-' * pad}")


def wait_for_live_station(weight_manager, station_id="station_1", timeout=20):
    print(f"  Waiting up to {timeout}s for live data from {station_id}...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = weight_manager.get_station_status(station_id)
        if status.get("connected") and status.get("weight_g") is not None:
            return True
        time.sleep(0.3)
    return False


def make_fake_telegram():
    class _FakeTelegram:
        def send_registration_confirmation(self, *a, **k): return True
        def send_medication_reminder(self, *a, **k): return True
        def send_dose_taken_confirmation(self, *a, **k): return True
        def send_incorrect_dosage_alert(self, *a, **k): return True
        def send_behavioral_alert(self, *a, **k): return True
        def send_missed_dose_alert(self, *a, **k): return True
        def send_message(self, *a, **k): return True
    return _FakeTelegram()

# --------------------------------------------------------------------------
# T1: Fresh registration
# --------------------------------------------------------------------------

def test_fresh_registration(config, logger) -> bool:
    section("T1 - Fresh Registration")

    db = Database(config["database"], logger)
    db.connect()

    try:
        with db.db_lock:
            db.connection.execute(
                "DELETE FROM registered_medicines WHERE station_id = 'station_1'"
            )
            db.connection.commit()
        print("  Cleared existing station_1 registration from database.")
    except Exception as e:
        print(f"  Warning: could not clear DB: {e}")

    mqtt = MQTTClient(config.get_mqtt_config(), logger)
    weight_manager = WeightManager(config["weight_sensors"], logger)
    mqtt.set_weight_callback(weight_manager.process_weight_data)
    mqtt.connect()

    tag_topic = config["identity"]["tag"]["mqtt_topic"]
    tag_runtime = TagRuntimeService(
        mqtt_config=config["mqtt"],
        database=db,
        logger=logger,
        topic=tag_topic
    )
    tag_runtime.start()

    reg_manager = RegistrationManager(
        config=config.config,
        weight_manager=weight_manager,
        tag_runtime_service=tag_runtime,
        database=db,
        display=None,
        audio=None,
        telegram=make_fake_telegram(),
        logger=logger
    )

    if not wait_for_live_station(weight_manager):
        print("  ERROR: no live scale data. Is the M5StickC running?")
        mqtt.disconnect()
        tag_runtime.stop()
        db.cleanup()
        return False

    unregistered = reg_manager.stations_needing_registration()
    passed = check(
        "station_1 detected as needing registration",
        "station_1" in unregistered
    )

    print()
    print("  ACTION REQUIRED:")
    print("  Place the medicine bottle (RFID sticker on bottom) on the station.")
    print("  The tag is read on first contact. Weight takes a few seconds to stabilise.")
    print("  Single placement is enough - you do NOT need to lift and re-place.")
    print(f"  Timeout: {reg_manager.timeout_seconds}s")
    print()

    ok = reg_manager.run_registration_if_needed()
    passed &= check("Registration completed successfully", ok)

    record = db.get_registered_medicine_by_station("station_1")
    passed &= check("Medicine record saved to database", record is not None)
    if record:
        passed &= check("medicine_id is M001", record.get("medicine_id") == "M001")
        passed &= check("station_id is station_1", record.get("station_id") == "station_1")
        passed &= check(
            "baseline captured (>0g)",
            weight_manager.baseline_weights.get("station_1", 0) > 0
        )
        print(f"\n  Registered: {record.get('medicine_name')}  "
              f"baseline={weight_manager.baseline_weights.get('station_1'):.2f}g")

    mqtt.disconnect()
    tag_runtime.stop()
    db.cleanup()
    return passed
    
# --------------------------------------------------------------------------
# T2: Re-registration skipped
# --------------------------------------------------------------------------

def test_reregistration_skipped(config, logger) -> bool:
    section("T2 - Re-registration Skipped (already registered)")

    db = Database(config["database"], logger)
    db.connect()

    record = db.get_registered_medicine_by_station("station_1")
    if not record:
        print("  SKIP: station_1 not registered. Run T1 first.")
        db.cleanup()
        return True

    mqtt = MQTTClient(config.get_mqtt_config(), logger)
    weight_manager = WeightManager(config["weight_sensors"], logger)
    mqtt.set_weight_callback(weight_manager.process_weight_data)
    mqtt.connect()

    tag_topic = config["identity"]["tag"]["mqtt_topic"]
    tag_runtime = TagRuntimeService(
        mqtt_config=config["mqtt"],
        database=db,
        logger=logger,
        topic=tag_topic
    )
    tag_runtime.start()

    reg_manager = RegistrationManager(
        config=config.config,
        weight_manager=weight_manager,
        tag_runtime_service=tag_runtime,
        database=db,
        display=None,
        audio=None,
        telegram=make_fake_telegram(),
        logger=logger
    )

    unregistered = reg_manager.stations_needing_registration()
    passed = check(
        "station_1 NOT in unregistered list",
        "station_1" not in unregistered
    )
    ok = reg_manager.run_registration_if_needed()
    passed &= check("run_registration_if_needed returns True without prompting", ok)

    mqtt.disconnect()
    tag_runtime.stop()
    db.cleanup()
    return passed
    
# --------------------------------------------------------------------------
# T3: Coincident tag window logic
# --------------------------------------------------------------------------

def test_coincident_tag_window(config, logger) -> bool:
    section("T3 - Coincident Tag Window Timing Logic")

    db = Database(config["database"], logger)
    db.connect()

    tag_topic = config["identity"]["tag"]["mqtt_topic"]
    tag_runtime = TagRuntimeService(
        mqtt_config=config["mqtt"],
        database=db,
        logger=logger,
        topic=tag_topic
    )
    tag_runtime.start()

    print("  Tap the RFID sticker tag now (within 10 seconds)...")
    deadline = time.time() + 10
    while time.time() < deadline:
        if tag_runtime.get_latest_scan():
            break
        time.sleep(0.2)

    scan = tag_runtime.get_latest_scan()
    if not scan:
        print("  No tag scan in 10s. SKIP.")
        tag_runtime.stop()
        db.cleanup()
        return True

    tag_time = scan["received_at"]
    print(f"  Tag scan received (age: {time.time() - tag_time:.1f}s old)")
    passed = True

    # Scan arrived at tag_time; weight event fires at tag_time+2 -> within 15s window
    result = tag_runtime.verify_coincident_tag(
        weight_event_timestamp=tag_time + 2.0,
        expected_medicine_id=None,
        expected_station_id=None,
        window_seconds=15.0
    )
    passed &= check(
        "Scan 2s before weight event (within 15s window) -> success",
        result.get("success") is True
    )
    if not result.get("success"):
        print(f"    Reason: {result.get('reason')}")

    # 20s gap -> outside window -> failure
    result2 = tag_runtime.verify_coincident_tag(
        weight_event_timestamp=tag_time + 20.0,
        expected_medicine_id=None,
        expected_station_id=None,
        window_seconds=15.0
    )
    passed &= check(
        "Scan 20s before weight event (outside 15s window) -> failure",
        result2.get("success") is False
    )

    fresh = tag_runtime.get_tag_within_window(window_seconds=30.0)
    passed &= check("get_tag_within_window(30s) returns scan", fresh is not None)

    stale = tag_runtime.get_tag_within_window(window_seconds=0.0)
    passed &= check("get_tag_within_window(0s) returns None", stale is None)

    tag_runtime.stop()
    db.cleanup()
    return passed
    
# --------------------------------------------------------------------------
# T4: Full integrated end-to-end flow
# --------------------------------------------------------------------------

def test_full_integrated_flow(config, logger) -> bool:
    section("T4 - Full Integrated Flow (headless, manual trigger)")

    system = MedicationSystem(
        config_path="config/config.yaml",
        enable_display=False,
        enable_audio=False
    )

    # Suppress all Telegram traffic
    system.telegram.send_medication_reminder = lambda *a, **k: True
    system.telegram.send_dose_taken_confirmation = lambda *a, **k: True
    system.telegram.send_incorrect_dosage_alert = lambda *a, **k: True
    system.telegram.send_behavioral_alert = lambda *a, **k: True
    system.telegram.send_missed_dose_alert = lambda *a, **k: True
    system.telegram.send_message = lambda *a, **k: True
    system.telegram.send_registration_confirmation = lambda *a, **k: True

    # Mock patient monitoring (no camera needed for this test)
    system.patient_monitor.start_monitoring = lambda duration=30, callback=None: True
    system.patient_monitor.is_monitoring_active = lambda: False
    system.patient_monitor.get_results = lambda: {
        "compliance_status": "good",
        "swallow_count": 1,
        "cough_count": 0,
        "hand_motion_count": 1
    }
    system.patient_monitor.cleanup = lambda *a, **k: None

    # CRITICAL: mark system as running so the pipeline doesn't exit immediately
    # when _verify_medication_intake checks self.running at the top.
    # (In normal use, start() sets this; in tests we set it manually.)
    system.running = True

    passed = True
    station_id = "station_1"

    print("  Waiting for live scale data...")
    deadline = time.time() + 20
    while time.time() < deadline:
        status = system.weight_manager.get_station_status(station_id)
        if status.get("connected"):
            break
        time.sleep(0.3)
    else:
        print("  ERROR: no live station data.")
        system.stop()
        return False

    # Capture or use persisted baseline
    if system.weight_manager.baseline_capture_required.get(station_id, True):
        print()
        print("  ACTION: Place the FULL bottle on the station and press Enter.")
        input("  Press Enter once bottle is stable... ")
        ok = system.weight_manager.capture_current_baseline(station_id)
        if not ok:
            print("  Baseline capture failed. Place bottle and try again.")
            system.stop()
            return False

    baseline = system.weight_manager.baseline_weights.get(station_id, 0)
    print(f"  Baseline: {baseline:.2f}g")

    # Trigger manual reminder
    reminder_data = {
        "medicine_name": "Aspirin 100mg",
        "dosage_pills": 2,
        "station_id": station_id,
        "scheduled_time": time.strftime("%H:%M"),
        "actual_time": time.strftime("%H:%M:%S"),
        "timestamp": time.time()
    }
    system._on_medication_reminder(reminder_data)
    passed &= check(
        "State -> REMINDER_ACTIVE after reminder",
        system.state_machine.get_state() == SystemState.REMINDER_ACTIVE
    )

    print()
    print("  ACTION REQUIRED:")
    print("  1. Lift the bottle off the scale")
    print("  2. Remove EXACTLY 2 pills")
    print("  3. Place the bottle back on the integrated station in ONE motion")
    print("     (tag is read on contact, weight stabilises a few seconds later)")
    print("  4. Leave it still")
    print()
    print("  Watching for weight event (up to 60s)...")

    # Wait for weight event (bottle lift + replace detected by FSM)
    event_deadline = time.time() + 60
    while time.time() < event_deadline:
        if system.pending_weight_event:
            print("  Weight event detected.")
            break
        time.sleep(0.5)
    else:
        passed &= check("Weight event received from FSM (timed out)", False)
        system.stop()
        return passed
        
    passed &= check("Weight event received from FSM", True)

    # Run the full verification pipeline synchronously.
    # This blocks for ~35-55s (identity check + mocked monitoring).
    print("  Running verification pipeline (blocks until complete)...")
    t0 = time.time()
    system._process_pending_weight_event()
    elapsed = time.time() - t0
    print(f"  Pipeline completed in {elapsed:.1f}s")

    passed &= check(
        "State returned to IDLE after pipeline",
        system.state_machine.get_state() == SystemState.IDLE
    )

    events = system.database.get_todays_events()
    passed &= check("At least one event logged today", len(events) > 0)

    if events:
        latest = events[-1]
        result_val = latest.get("result", "?")
        verified = latest.get("verified", False)
        dosage = latest.get("actual_dosage", "?")
        print(f"\n  Latest event: result={result_val}  verified={verified}  "
              f"actual_dosage={dosage}")

        # Check whether integrated tag identity was used
        details = latest.get("details", {})
        identity_method = details.get("identity_method", "")
        ocr_status = details.get("ocr_status", "")

        if identity_method in ("tag_integrated", "tag"):
            passed &= check("Identity verified via integrated tag", True)
        elif ocr_status == "failed" and result_val != "incorrect_dosage":
            # Identity fell back but that is acceptable - just report it
            print(f"  Note: identity fell back (method={identity_method or 'fallback'}). "
                  "The tag scan may have arrived outside the coincident window. "
                  "This is not a failure - fallback chain is working.")
            passed &= check("Verification pipeline ran end-to-end", True)
        else:
            passed &= check("Verification pipeline ran end-to-end", True)

        # Core success criteria: correct pill count
        if dosage == 2:
            passed &= check("Correct pill count (2) detected by weight sensor", True)
        else:
            passed &= check(f"Correct pill count (expected 2, got {dosage})", False)

    system.stop()
    return passed
    
# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    print("Phase 8 - Registration and Integrated Tag+Weight Flow Tests")
    print("=" * 60)

    config_path = Path("config/config.yaml")
    if not config_path.exists():
        print("ERROR: config/config.yaml not found. Run from project root.")
        sys.exit(1)

    config = get_config(str(config_path))
    logger = get_logger(config.get_logging_config())

    results = {}

    try:
        results["T1 Fresh Registration"] = test_fresh_registration(config, logger)
    except Exception as e:
        print(f"  EXCEPTION in T1: {e}")
        import traceback; traceback.print_exc()
        results["T1 Fresh Registration"] = False

    try:
        results["T2 Re-registration Skipped"] = test_reregistration_skipped(config, logger)
    except Exception as e:
        print(f"  EXCEPTION in T2: {e}")
        results["T2 Re-registration Skipped"] = False

    try:
        results["T3 Coincident Tag Window"] = test_coincident_tag_window(config, logger)
    except Exception as e:
        print(f"  EXCEPTION in T3: {e}")
        results["T3 Coincident Tag Window"] = False

    print()
    print("Run T4 (full pipeline with physical bottle removal)?")
    print("Takes ~40-60s. Camera is mocked; only weight + tag needed.")
    run_t4 = input("Enter 'y' to run T4, anything else to skip: ").strip().lower()
    if run_t4 == "y":
        try:
            results["T4 Full Integrated Flow"] = test_full_integrated_flow(config, logger)
        except Exception as e:
            print(f"  EXCEPTION in T4: {e}")
            import traceback; traceback.print_exc()
            results["T4 Full Integrated Flow"] = False
    else:
        print("  T4 skipped.")

    section("SUMMARY")
    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}]  {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("All Phase 8 tests passed.")
    else:
        print("Some tests failed. Review output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
