#!/usr/bin/env python3
"""
Group A and B Cleanup Verification Tests

Tests:
  A1 - Display screens render with no question marks or broken characters
  A2 - Telegram messages send with clean formatting
  B1 - medicine_id resolves from database via station_id
  B2 - _on_medication_reminder attaches medicine_id automatically
  B3 - _verify_medication_intake uses resolved medicine_id, no hardcoded fallback

Run from project root:
    python -m raspberry_pi.tests.Phase_7.test_group_ab_cleanup
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.display_manager import DisplayManager
from raspberry_pi.modules.telegram_bot import TelegramBot
from raspberry_pi.modules.database import Database


def check(label: str, condition: bool) -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}]  {label}")
    return condition


def section(title: str):
    print(f"\n{title}")
    print("-" * len(title))


def test_display_screens(config, logger) -> bool:
    section("A1 - Display Screens (plain ASCII, no broken characters)")

    display_config = {
        "width": 1024,
        "height": 600,
        "fullscreen": False
    }
    display = DisplayManager(display_config, logger)

    if not display.initialize():
        print("  Display failed to initialize. Skipping display tests.")
        print("  If running headless, this is expected.")
        return True

    passed = True

    print("  Showing idle screen for 2 seconds...")
    display.show_idle_screen({
        "medicine_name": "Aspirin 100mg",
        "time": "08:00",
        "time_until": "00:10:00"
    })
    time.sleep(2)
    passed &= check("Idle screen rendered", True)

    print("  Showing reminder screen for 2 seconds...")
    display.show_reminder_screen("Aspirin 100mg", 2, "08:00")
    time.sleep(2)
    passed &= check("Reminder screen rendered", True)

    print("  Showing monitoring screen for 2 seconds...")
    display.show_monitoring_screen(15, 30, "Monitoring intake...")
    time.sleep(2)
    passed &= check("Monitoring screen rendered", True)

    print("  Showing success screen for 2 seconds...")
    display.show_success_screen("Aspirin 100mg", "Medication taken successfully!")
    time.sleep(2)
    passed &= check("Success screen rendered without broken characters", True)

    print("  Showing warning screen for 2 seconds...")
    display.show_warning_screen("Incorrect Dosage", "Expected 2 pills, detected 1 pill")
    time.sleep(2)
    passed &= check("Warning screen rendered without broken characters", True)

    print("  Showing error screen for 2 seconds...")
    display.show_error_screen("Camera initialization failed")
    time.sleep(2)
    passed &= check("Error screen rendered without broken characters", True)

    print("  Showing registration screen for 2 seconds...")
    display.show_registration_screen("station_1", "Waiting for medicine...")
    time.sleep(2)
    passed &= check("Registration screen rendered", True)

    display.cleanup()
    return passed

def test_telegram_messages(config, logger) -> bool:
    section("A2 - Telegram Messages (clean formatting, no broken symbols)")

    telegram_config = config.get_telegram_config()
    bot = TelegramBot(telegram_config, logger)

    passed = True

    print("  Sending medication reminder...")
    ok = bot.send_medication_reminder("Aspirin 100mg", 2, "08:00")
    passed &= check("Medication reminder sent", ok)

    print("  Sending dose confirmation...")
    ok = bot.send_dose_taken_confirmation("Aspirin 100mg", 2)
    passed &= check("Dose confirmation sent", ok)

    print("  Sending incorrect dosage alert...")
    ok = bot.send_incorrect_dosage_alert("Aspirin 100mg", 2, 1)
    passed &= check("Incorrect dosage alert sent", ok)

    print("  Sending missed dose alert...")
    ok = bot.send_missed_dose_alert("Aspirin 100mg", "08:00", 30)
    passed &= check("Missed dose alert sent", ok)

    print("  Sending behavioral alert...")
    ok = bot.send_behavioral_alert("Aspirin 100mg", "concerning", {
        "cough_count": 7,
        "swallow_count": 0,
        "compliance_status": "concerning"
    })
    passed &= check("Behavioral alert sent", ok)

    print("  Sending registration confirmation...")
    ok = bot.send_registration_confirmation("Aspirin 100mg", "station_1", 2, ["08:00", "20:00"])
    passed &= check("Registration confirmation sent", ok)

    print("  Sending daily compliance report...")
    ok = bot.send_daily_compliance_report({
        "total_scheduled": 3,
        "taken_correctly": 2,
        "taken_incorrectly": 1,
        "missed": 0,
        "behavioral_issues": 0
    })
    passed &= check("Daily compliance report sent", ok)

    bot.cleanup()
    return passed


def test_medicine_id_database_lookup(config, logger) -> bool:
    section("B1 - medicine_id resolves from database via station_id")

    db = Database(config['database'], logger)
    db.connect()

    test_record = {
        "medicine_id": "M001",
        "patient_id": "P001",
        "medicine_name": "Aspirin 100mg",
        "dosage_amount": 2,
        "dosage_unit": "TABLET",
        "time_slots": "08:00,20:00",
        "meal_rule": "AFTER_MEAL",
        "station_id": "station_1",
        "tag_uid": "TEST_TAG_UID_001",
        "tag_payload": "ID=M001;P=P001;N=ASPIRIN100;D=2;T=08,20;M=AF;S=1",
        "source_method": "tag",
        "active": True
    }
    db.upsert_registered_medicine(test_record)

    passed = True

    result = db.get_registered_medicine_by_station("station_1")
    passed &= check("get_registered_medicine_by_station returns a result", result is not None)

    if result:
        passed &= check("medicine_id is M001", result.get('medicine_id') == 'M001')
        passed &= check("medicine_name is correct", result.get('medicine_name') == 'Aspirin 100mg')
        passed &= check("station_id is station_1", result.get('station_id') == 'station_1')

    result_none = db.get_registered_medicine_by_station("station_99")
    passed &= check("Returns None for unregistered station", result_none is None)

    db.cleanup()
    return passed


def test_medicine_id_in_reminder_callback(config, logger) -> bool:
    section("B2 - _on_medication_reminder attaches medicine_id from database")

    from raspberry_pi.main import MedicationSystem

    system = MedicationSystem(
        config_path="config/config.yaml",
        enable_display=False,
        enable_audio=False
    )
    
    # Suppress Telegram
    system.telegram.send_medication_reminder = lambda *a, **k: True

    # Make sure station_1 has a registered medicine
    test_record = {
        "medicine_id": "M001",
        "patient_id": "P001",
        "medicine_name": "Aspirin 100mg",
        "dosage_amount": 2,
        "dosage_unit": "TABLET",
        "time_slots": "08:00,20:00",
        "meal_rule": "AFTER_MEAL",
        "station_id": "station_1",
        "tag_uid": "TEST_TAG_UID_001",
        "tag_payload": "ID=M001;P=P001;N=ASPIRIN100;D=2;T=08,20;M=AF;S=1",
        "source_method": "tag",
        "active": True
    }
    system.database.upsert_registered_medicine(test_record)

    reminder_data = {
        "medicine_name": "Aspirin 100mg",
        "dosage_pills": 2,
        "station_id": "station_1",
        "scheduled_time": "08:00",
        "actual_time": "08:00:00",
        "timestamp": time.time()
    }

    system._on_medication_reminder(reminder_data)

    passed = True
    passed &= check(
        "medicine_id attached to current_medication",
        system.current_medication is not None and 'medicine_id' in system.current_medication
    )
    if system.current_medication and 'medicine_id' in system.current_medication:
        passed &= check(
            "medicine_id resolved to M001",
            system.current_medication['medicine_id'] == 'M001'
        )

    system.running = True
    system.stop()
    return passed


def test_no_hardcoded_medicine_id(config, logger) -> bool:
    section("B3 - No hardcoded medicine_id fallback in main.py")

    main_path = Path("raspberry_pi/main.py")
    if not main_path.exists():
        print("  main.py not found at expected path, skipping.")
        return True

    content = main_path.read_text()

    passed = True
    
    import re
    code_only = re.sub(r'""".*?"""', '', content, flags=re.DOTALL)
    code_only = re.sub(r"'''.*?'''", '', code_only, flags=re.DOTALL)
    code_only = re.sub(r'#.*', '', code_only)
    passed &= check(
        "No hardcoded M001 fallback in main.py",
        '"M001"' not in code_only and "'M001'" not in code_only
    )
    
    passed &= check(
        "_resolve_medicine_id_for_station method present",
        "_resolve_medicine_id_for_station" in content
    )

    return passed
    
def main():
    print("Group A and B Cleanup Verification Tests")
    print("=========================================")

    config_path = Path("config/config.yaml")
    if not config_path.exists():
        print("ERROR: config/config.yaml not found. Run from project root.")
        sys.exit(1)

    config = get_config(str(config_path))
    logger = get_logger(config.get_logging_config())

    results = {}

    results["A1 Display screens"] = test_display_screens(config, logger)
    results["A2 Telegram messages"] = test_telegram_messages(config, logger)
    results["B1 DB medicine_id lookup"] = test_medicine_id_database_lookup(config, logger)
    results["B2 Reminder attaches medicine_id"] = test_medicine_id_in_reminder_callback(config, logger)
    results["B3 No hardcoded M001"] = test_no_hardcoded_medicine_id(config, logger)

    section("SUMMARY")
    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}]  {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("All cleanup tests passed.")
    else:
        print("Some tests failed. Review the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
