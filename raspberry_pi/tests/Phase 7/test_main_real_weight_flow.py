import time

from raspberry_pi.main import MedicationSystem, SystemState


def wait_for_live_data(system, station_id, timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        status = system.weight_manager.get_station_status(station_id)
        if status.get("connected") and status.get("weight_g") is not None:
            return True
        time.sleep(0.2)
    return False


def main():
    print("Starting main real-weight workflow test...")
    print("This test uses REAL MQTT + REAL weight data from station_1.")
    print("Display and audio are disabled completely for clean testing.\n")

    system = MedicationSystem(
        config_path="config/config.yaml",
        enable_display=False,
        enable_audio=False
    )

    # Disable Telegram
    system.telegram.send_medication_reminder = lambda *args, **kwargs: True
    system.telegram.send_dose_taken_confirmation = lambda *args, **kwargs: True
    system.telegram.send_incorrect_dosage_alert = lambda *args, **kwargs: True
    system.telegram.send_behavioral_alert = lambda *args, **kwargs: True
    system.telegram.send_missed_dose_alert = lambda *args, **kwargs: True
    system.telegram.send_message = lambda *args, **kwargs: True

    # Mock OCR
    system.scanner.initialize_camera = lambda: True
    system.scanner.scan_label = lambda num_attempts=2: {
        "success": True,
        "medicine_name": "Aspirin 100mg",
        "confidence": 0.95
    }
    system.scanner.release_camera = lambda: None

    # Mock patient monitoring
    system.patient_monitor.start_monitoring = lambda duration=30, callback=None: True
    system.patient_monitor.is_monitoring_active = lambda: False
    system.patient_monitor.get_results = lambda: {
        "compliance_status": "good",
        "swallow_count": 1,
        "cough_count": 0,
        "hand_motion_count": 1
    }
    system.patient_monitor.cleanup = lambda *args, **kwargs: None

    station_id = "station_1"

    print("Waiting for live station data...")
    if not wait_for_live_data(system, station_id):
        print("ERROR: no live station data received.")
        system.stop()
        return

    print("Current station status at test start:")
    print(system.weight_manager.get_station_status(station_id))

    print("\nIMPORTANT:")
    print("1. Put the FULL bottle on station_1 now.")
    print("2. Wait until it is stable.")
    print("3. Only then press Enter to capture fresh baseline.")
    input("Press Enter to capture fresh baseline now...")

    ok = system.weight_manager.capture_current_baseline(station_id)
    if not ok:
        print("Fresh baseline capture failed.")
        system.stop()
        return

    print("Fresh baseline captured.")
    print(system.weight_manager.get_station_status(station_id))

    reminder_data = {
        "medicine_name": "Aspirin 100mg",
        "dosage_pills": 2,
        "station_id": "station_1",
        "scheduled_time": "08:00",
        "actual_time": time.strftime("%H:%M:%S"),
        "timestamp": time.time()
    }

    system._on_medication_reminder(reminder_data)

    print("\nSystem is now armed.")
    print("Now do this physically:")
    print("- remove EXACTLY 2 pills")
    print("- return bottle gently to the SAME position")
    print("- leave it untouched to settle\n")

    start = time.time()
    timeout_seconds = 90
    
    try:
        while time.time() - start < timeout_seconds:
            system._process_pending_weight_event()

            state_name = system.state_machine.get_state_name()
            station_status = system.weight_manager.get_station_status(station_id)
            latest_event = system.weight_manager.last_event_data.get(station_id)

            print(
                f"[WAIT] state={state_name} "
                f"weight={station_status.get('weight_g')}g "
                f"stable={station_status.get('stable')} "
                f"baseline={station_status.get('baseline_g')} "
                f"last_event={latest_event}"
            )

            if (
                system.current_medication is None
                and system.state_machine.get_state() == SystemState.IDLE
            ):
                print("\nWorkflow returned to IDLE. Checking database...")
                break

            time.sleep(1)
        else:
            print("\nTimeout waiting for workflow to complete.")

        todays_events = system.database.get_todays_events()
        print(f"\nToday's events count: {len(todays_events)}")

        if todays_events:
            latest = todays_events[-1]
            print("Latest event:")
            print(latest)

    except KeyboardInterrupt:
        print("\nStopped by user.")

    finally:
        system.stop()
        print("Main real-weight workflow test completed.")


if __name__ == "__main__":
    main()
