#!/usr/bin/env python3
"""
Full app manual trigger test

Uses the REAL app with display enabled.
Display stays on the main thread.
A helper thread queues a manual reminder after startup so the real app loop
can process it safely.
"""

import time
import threading

from raspberry_pi.main import MedicationSystem, SystemState


def wait_for_live_station(system, station_id="station_1", timeout=20):
    print("Waiting for live station data...")
    start = time.time()

    while time.time() - start < timeout:
        status = system.weight_manager.get_station_status(station_id)
        if status.get("connected") and status.get("weight_g") is not None:
            return status
        time.sleep(0.2)

    return None


def reminder_worker(system, reminder_data, delay_seconds=1.0):
    time.sleep(delay_seconds)
    system.queue_manual_reminder(reminder_data)


def watcher_worker(system, station_id="station_1", timeout_seconds=120):
    start = time.time()
    reminder_seen = False

    while time.time() - start < timeout_seconds:
        state_name = system.state_machine.get_state_name()
        station_status = system.weight_manager.get_station_status(station_id)
        latest_event = system.weight_manager.last_event_data.get(station_id)
        armed = station_status.get("event_detection_enabled")

        print(
            f"[WAIT] state={state_name} "
            f"weight={station_status.get('weight_g')}g "
            f"stable={station_status.get('stable')} "
            f"baseline={station_status.get('baseline_g')} "
            f"armed={armed} "
            f"last_event={latest_event}"
        )

        if state_name == "REMINDER_ACTIVE":
            reminder_seen = True

        if reminder_seen and system.current_medication is None and system.state_machine.get_state() == SystemState.IDLE:
            print("\nWorkflow returned to IDLE. Checking database...")
            try:
                todays_events = system.database.get_todays_events()
                print(f"\nToday's events count: {len(todays_events)}")

                if todays_events:
                    latest = todays_events[-1]
                    print("Latest event:")
                    print(latest)
            finally:
                system.stop()
            return

        time.sleep(1)

    print("\nTimeout waiting for workflow to complete.")
    try:
        todays_events = system.database.get_todays_events()
        print(f"\nToday's events count: {len(todays_events)}")
        if todays_events:
            latest = todays_events[-1]
            print("Latest event:")
            print(latest)
    except Exception as e:
        print(f"Failed to read database at timeout: {e}")
    finally:
        system.stop()


def main():
    print("Starting full app manual trigger test...")
    print("This uses the REAL app with display enabled.")
    print("It manually triggers the medication reminder so you can test now.\n")

    system = MedicationSystem(
        config_path="config/config.yaml",
        enable_display=True,
        enable_audio=True
    )
    
    # Optional: avoid Telegram spam during repeated testing
    system.telegram.send_medication_reminder = lambda *args, **kwargs: True
    system.telegram.send_dose_taken_confirmation = lambda *args, **kwargs: True
    system.telegram.send_incorrect_dosage_alert = lambda *args, **kwargs: True
    system.telegram.send_behavioral_alert = lambda *args, **kwargs: True
    system.telegram.send_missed_dose_alert = lambda *args, **kwargs: True
    system.telegram.send_message = lambda *args, **kwargs: True

    station_id = "station_1"

    try:
        status = wait_for_live_station(system, station_id=station_id, timeout=20)
        if not status:
            print("Timeout waiting for live station data.")
            return

        print("Current station status:")
        print(status)

        print("\nIMPORTANT:")
        print("1. Put the FULL bottle on station_1 now.")
        print("2. Wait until it is stable.")
        print("3. Only then press Enter to capture fresh baseline.")
        input("Press Enter to capture fresh baseline now...")

        ok = system.weight_manager.capture_current_baseline(station_id)
        if not ok:
            print("Fresh baseline capture failed.")
            print("Make sure the full bottle is on the station and stable.")
            return

        print("Fresh baseline captured.")
        print(system.weight_manager.get_station_status(station_id))

        reminder_data = {
            "medicine_name": "Aspirin 100mg",
            "dosage_pills": 2,
            "station_id": "station_1",
            "scheduled_time": time.strftime("%H:%M"),
            "actual_time": time.strftime("%H:%M:%S"),
            "timestamp": time.time()
        }

        print("\nReminder will be triggered automatically after app startup.")
        print("Now physically test one of these after the reminder appears:")
        print("A) remove EXACTLY 2 pills -> success path")
        print("B) remove 1 pill -> wrong-dose path")
        print("C) do nothing -> remains waiting")
        print("\nWatch both the terminal and the display.\n")

        threading.Thread(
            target=reminder_worker,
            args=(system, reminder_data, 1.0),
            daemon=True
        ).start()

        threading.Thread(
            target=watcher_worker,
            args=(system, station_id, 120),
            daemon=True
        ).start()

        # IMPORTANT: keep display loop on MAIN THREAD
        system.start()

    except KeyboardInterrupt:
        print("\nStopped by user.")
        system.stop()
    finally:
        print("Full app manual trigger test completed.")


if __name__ == "__main__":
    main()
