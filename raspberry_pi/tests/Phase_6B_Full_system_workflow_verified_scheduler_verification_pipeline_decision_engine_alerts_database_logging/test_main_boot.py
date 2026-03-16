from raspberry_pi.main import MedicationSystem


def main():
    print("Starting main boot smoke test...")

    system = MedicationSystem(config_path="config/config.yaml")

    print("MedicationSystem created successfully.")
    print("Running state:", system.running)
    print("Current state:", system.state_machine.get_state_name())
    print("Current medication:", system.current_medication)

    # Force cleanup because stop() only works when running=True
    system.running = True
    system.stop()

    print("Main boot smoke test completed.")


if __name__ == "__main__":
    main()
