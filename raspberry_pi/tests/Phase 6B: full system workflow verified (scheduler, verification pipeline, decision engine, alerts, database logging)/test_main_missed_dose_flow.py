from raspberry_pi.main import MedicationSystem


def main():
    print("Starting main missed-dose callback test...")

    system = MedicationSystem(config_path="config/config.yaml")

    missed_data = {
        "medicine_name": "AMLODIPINE",
        "scheduled_time": "08:00",
        "timeout_minutes": 30
    }

    system._on_missed_dose(missed_data)

    print("Current medication:", system.current_medication)
    print("Current state:", system.state_machine.get_state_name())

    todays_events = system.database.get_todays_events()
    print("Today's events count:", len(todays_events))

    system.running = True
    system.stop()

    print("Main missed-dose callback test completed.")


if __name__ == "__main__":
    main()
