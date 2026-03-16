from raspberry_pi.main import MedicationSystem, SystemState


def main():
    print("Starting main incorrect-dosage workflow test...")

    system = MedicationSystem(config_path="config/config.yaml")

    system.current_medication = {
        "medicine_name": "AMLODIPINE",
        "dosage_pills": 2,
        "station_id": "station_1",
        "scheduled_time": "08:00",
        "actual_time": "08:00:00",
        "timestamp": 1234567890.0
    }

    system.state_machine.transition_to(SystemState.REMINDER_ACTIVE, system.current_medication)

    system.scanner.initialize_camera = lambda: True
    system.scanner.scan_label = lambda num_attempts=2: {
        "success": True,
        "medicine_name": "AMLODIPINE",
        "confidence": 0.95
    }
    system.scanner.release_camera = lambda: None

    system.weight_manager.verify_dosage = lambda station_id, expected_dosage: {
        "verified": True,
        "actual": 1,
        "weight_actual": 1
    }

    system.patient_monitor.start_monitoring = lambda duration=30, callback=None: True
    system.patient_monitor.is_monitoring_active = lambda: False
    system.patient_monitor.get_results = lambda: {
        "compliance_status": "good",
        "swallow_count": 1,
        "cough_count": 0,
        "hand_motion_count": 1
    }

    event_data = {
        "station_id": "station_1",
        "pills_removed": 1,
        "weight_change_g": 0.5,
        "current_weight_g": 10.0,
        "timestamp": 1234567890.0
    }

    system._on_pill_removal(event_data)

    print("Final state:", system.state_machine.get_state_name())
    print("Current medication:", system.current_medication)

    todays_events = system.database.get_todays_events()
    print("Today's events count:", len(todays_events))
    if todays_events:
        print("Latest event:", todays_events[-1])

    system.running = True
    system.stop()

    print("Main incorrect-dosage workflow test completed.")


if __name__ == "__main__":
    main()
