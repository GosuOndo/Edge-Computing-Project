#!/usr/bin/env python3
"""
Phase 9 - Onboarding Flow Test

Tests the full sequential onboarding of 3 medicines on station_1.
Requires:
  - M5StickC scale running and publishing to MQTT
  - RFID reader running and publishing to MQTT
  - 3 medicine bottles with correct tag payloads:
      Tag 1: ID=M001;P=P001;N=ASPIRIN100;D=2;T=08,20;M=AF;S=1
      Tag 2: ID=M002;P=P001;N=AMLODIPINE5;D=1;T=08;M=AF;S=1
      Tag 3: ID=M003;P=P001;N=PARA500;D=2;T=20;M=AF;S=1

Run from project root:
    python -m raspberry_pi.tests.Phase_9.test_onboarding_flow
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
from raspberry_pi.modules.telegram_bot import TelegramBot
from raspberry_pi.services.mqtt_client import MQTTClient
from raspberry_pi.services.scheduler import MedicationScheduler


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


def wait_for_live_station(weight_manager, station_id, timeout=20):
    print(f"  Waiting up to {timeout}s for live data from {station_id}...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = weight_manager.get_station_status(station_id)
        if status.get("connected") and status.get("weight_g") is not None:
            return True
        time.sleep(0.3)
    return False
    
# ------------------------------------------------------------------ #
# Test: verify tags read correctly before onboarding
# ------------------------------------------------------------------ #

def test_tag_reads(config, logger, tag_runtime) -> bool:
    section("T1 - Verify All 3 Tags Read Correctly")

    expected = {
        "M001": "ASPIRIN100",
        "M002": "AMLODIPINE5",
        "M003": "PARA500",
    }

    results = {}

    for medicine_id, medicine_name in expected.items():
        print(f"\n  Tap the {medicine_name} tag now...")
        tag_runtime.clear_latest_scan()

        deadline = time.time() + 15
        found = False
        while time.time() < deadline:
            scan = tag_runtime.get_latest_scan()
            if scan:
                payload = scan["scan_msg"].get("payload_raw", "")
                if medicine_id in payload:
                    print(f"  Received: {payload}")
                    found = True
                    break
            time.sleep(0.2)

        results[medicine_id] = found
        check(f"{medicine_name} ({medicine_id}) tag readable", found)
        if not found:
            print(f"  WARNING: {medicine_id} tag not detected within 15s")

    tag_runtime.clear_latest_scan()
    return all(results.values())


# ------------------------------------------------------------------ #
# Test: full onboarding sequence
# ------------------------------------------------------------------ #

def test_onboarding_sequence(config, logger) -> bool:
    section("T2 - Full Sequential Onboarding (3 medicines)")

    db = Database(config["database"], logger)
    db.connect()

    # Clear any existing registrations for a clean test
    try:
        with db.db_lock:
            db.connection.execute("DELETE FROM registered_medicines")
            db.connection.commit()
        print("  Existing registrations cleared.")
    except Exception as e:
        print(f"  Warning: could not clear registrations: {e}")

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

    scheduler = MedicationScheduler(config["schedule"], logger)
    telegram = TelegramBot(config.get_telegram_config(), logger)
    
    # Use a mock display and audio for headless testing
    # Replace with real instances if running with screen attached
    class _MockDisplay:
        def show_registration_screen(self, *a, **k): pass
        def show_registration_success_screen(self, *a, **k): pass
        def show_error_screen(self, *a, **k): pass
        def update(self): pass

    class _MockAudio:
        def speak(self, text, wait=True):
            print(f"  [AUDIO] {text}")
        def initialize(self): return True

    reg_manager = RegistrationManager(
        config=config.config,
        weight_manager=weight_manager,
        tag_runtime_service=tag_runtime,
        database=db,
        display=_MockDisplay(),
        audio=_MockAudio(),
        telegram=telegram,
        logger=logger
    )

    passed = True

    if not wait_for_live_station(weight_manager, "station_1"):
        print("  ERROR: no live data from station_1. Is M5StickC running?")
        mqtt.disconnect()
        tag_runtime.stop()
        db.cleanup()
        return False

    print("\n  Live scale data confirmed.")
    print()
    print("  INSTRUCTIONS:")
    print("  The system will now walk through 3 medicines one at a time.")
    print("  For each medicine:")
    print("    1. Place the bottle on the scale (tag reads automatically on contact)")
    print("    2. Wait for weight to stabilise")
    print("    3. The system confirms registration on screen")
    print("    4. Remove the bottle when prompted, then place the next one")
    print()

    # Run onboarding - this is the main test
    ok = reg_manager.run_onboarding_if_needed(
        station_id="station_1",
        expected_medicine_count=3,
        scheduler=scheduler
    )

    passed &= check("Onboarding completed without error", ok)

    # Verify database contents
    registered = db.list_registered_medicines()
    passed &= check("3 medicines saved to database", len(registered) == 3)

    medicine_ids = {r["medicine_id"] for r in registered}
    passed &= check("M001 (Aspirin) registered",    "M001" in medicine_ids)
    passed &= check("M002 (Amlodipine) registered", "M002" in medicine_ids)
    passed &= check("M003 (Paracetamol) registered","M003" in medicine_ids)

    # Verify each record has correct fields
    for r in registered:
        mid = r["medicine_id"]
        passed &= check(
            f"{mid} has station_id = station_1",
            r.get("station_id") == "station_1"
        )
        passed &= check(
            f"{mid} has dosage_amount > 0",
            (r.get("dosage_amount") or 0) > 0
        )
        passed &= check(
            f"{mid} has time_slots populated",
            bool(r.get("time_slots"))
        )
        passed &= check(
            f"{mid} baseline captured",
            weight_manager.baseline_weights.get("station_1", 0) > 0
        )

    # Verify scheduler was populated dynamically
    scheduled = scheduler.get_scheduled_medicines()
    passed &= check(
        "At least 1 medicine added to live scheduler",
        len(scheduled) > 0
    )
    print(f"\n  Scheduled medicines: {scheduled}")

    # Print full registered summary
    print("\n  Registered medicines:")
    for r in registered:
        print(
            f"    {r['medicine_id']} | {r['medicine_name']} | "
            f"dose={r['dosage_amount']} | times={r['time_slots']} | "
            f"tag_uid={r['tag_uid']}"
        )
        
    mqtt.disconnect()
    tag_runtime.stop()
    db.cleanup()
    return passed


# ------------------------------------------------------------------ #
# Test: verify schedule summary built correctly
# ------------------------------------------------------------------ #

def test_schedule_summary(config, logger) -> bool:
    section("T3 - Schedule Summary and Telegram Notification")

    db = Database(config["database"], logger)
    db.connect()

    registered = db.list_registered_medicines()

    if not registered:
        print("  No registered medicines found. Run T2 first.")
        db.cleanup()
        return True

    passed = True
    passed &= check(f"Found {len(registered)} registered medicines", len(registered) > 0)

    # Build schedule summary (same logic as main._build_schedule_summary)
    entries = []
    for m in registered:
        name = m.get("medicine_name", "Unknown")
        dosage = m.get("dosage_amount", "?")
        time_slots = m.get("time_slots", "")
        for t in time_slots.split(","):
            t = t.strip()
            if t:
                entries.append(f"{t} - {name} ({dosage} pill(s))")
    entries.sort()

    print("\n  Generated schedule summary:")
    for entry in entries:
        print(f"    {entry}")

    passed &= check("Schedule summary has at least 3 entries", len(entries) >= 3)

    # Send onboarding complete Telegram message
    print("\n  Sending onboarding complete Telegram message...")
    telegram = TelegramBot(config.get_telegram_config(), logger)
    ok = telegram.send_onboarding_complete(
        medicines=registered,
        schedule_summary=entries
    )
    passed &= check("Telegram onboarding summary sent", ok)
    telegram.cleanup()

    db.cleanup()
    return passed


# ------------------------------------------------------------------ #
# Test: duplicate registration guard
# ------------------------------------------------------------------ #

def test_duplicate_guard(config, logger) -> bool:
    section("T4 - Duplicate Registration Guard")

    db = Database(config["database"], logger)
    db.connect()

    registered_before = db.list_registered_medicines()
    count_before = len(registered_before)

    if count_before == 0:
        print("  No medicines registered. Run T2 first.")
        db.cleanup()
        return True

    # Try to upsert M001 again - should update, not duplicate
    existing = db.get_registered_medicine_by_id("M001")
    if existing:
        ok = db.upsert_registered_medicine(existing)
        registered_after = db.list_registered_medicines()
        count_after = len(registered_after)

        passed = check(
            f"Count unchanged after duplicate upsert ({count_before} ? {count_after})",
            count_before == count_after
        )
    else:
        print("  M001 not found. Skipping duplicate guard test.")
        passed = True

    db.cleanup()
    return passed
    
# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

def main():
    print("Phase 9 - Onboarding Flow Test Suite")
    print("=" * 64)

    config_path = Path("config/config.yaml")
    if not config_path.exists():
        print("ERROR: config/config.yaml not found. Run from project root.")
        sys.exit(1)

    config = get_config(str(config_path))
    logger = get_logger(config.get_logging_config())

    # Optional: run tag verification first
    print("\nRun T1 (tag read verification) before onboarding?")
    print("This lets you confirm all 3 tags are readable before committing.")
    run_t1 = input("Enter 'y' to run T1, anything else to skip: ").strip().lower()

    results = {}

    if run_t1 == "y":
        tag_topic = config["identity"]["tag"]["mqtt_topic"]
        db_temp = Database(config["database"], logger)
        db_temp.connect()
        tag_runtime_temp = TagRuntimeService(
            mqtt_config=config["mqtt"],
            database=db_temp,
            logger=logger,
            topic=tag_topic
        )
        tag_runtime_temp.start()

        try:
            results["T1 Tag Read Verification"] = test_tag_reads(
                config, logger, tag_runtime_temp
            )
        finally:
            tag_runtime_temp.stop()
            db_temp.cleanup()

    # Main onboarding test
    try:
        results["T2 Onboarding Sequence"] = test_onboarding_sequence(config, logger)
    except Exception as e:
        print(f"\n  EXCEPTION in T2: {e}")
        import traceback
        traceback.print_exc()
        results["T2 Onboarding Sequence"] = False

    # Schedule summary and Telegram
    try:
        results["T3 Schedule Summary"] = test_schedule_summary(config, logger)
    except Exception as e:
        print(f"\n  EXCEPTION in T3: {e}")
        results["T3 Schedule Summary"] = False

    # Duplicate guard
    try:
        results["T4 Duplicate Guard"] = test_duplicate_guard(config, logger)
    except Exception as e:
        print(f"\n  EXCEPTION in T4: {e}")
        results["T4 Duplicate Guard"] = False

    # Summary
    section("SUMMARY")
    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}]  {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("All Phase 9 tests passed. Ready to run full system.")
    else:
        print("Some tests failed. Review output above before running full system.")
        sys.exit(1)


if __name__ == "__main__":
    main()
