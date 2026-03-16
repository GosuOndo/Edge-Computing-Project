from raspberry_pi.main import MedicationSystem


def main():
    print("Starting main reminder callback test...")

    system = MedicationSystem(config_path="config/config.yaml")

    reminder_data = {
        "medicine_name": "AMLODIPINE",
        "dosage_pills": 1,
        "station_id": "station_1",
        "scheduled_time": "08:00",
        "actual_time": "08:00:00",
        "timestamp": 1234567890.0
    }

    system._on_medication_reminder(reminder_data)

    print("Current medication:", system.current_medication)
    print("Current state:", system.state_machine.get_state_name())

    system.running = True
    system.stop()

    print("Main reminder callback test completed.")


if __name__ == "__main__":
    main()
