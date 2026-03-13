from raspberry_pi.main import MedicationSystem


def main():
    print("Starting main wiring test...")

    system = MedicationSystem(config_path="config/config.yaml")

    modules = {
        "database": hasattr(system, "database"),
        "mqtt": hasattr(system, "mqtt"),
        "weight_manager": hasattr(system, "weight_manager"),
        "scanner": hasattr(system, "scanner"),
        "patient_monitor": hasattr(system, "patient_monitor"),
        "telegram": hasattr(system, "telegram"),
        "display": hasattr(system, "display"),
        "audio": hasattr(system, "audio"),
        "decision_engine": hasattr(system, "decision_engine"),
        "scheduler": hasattr(system, "scheduler"),
        "state_machine": hasattr(system, "state_machine"),
    }

    print("Wiring results:")
    for name, ok in modules.items():
        print(f"{name}: {ok}")

    system.running = True
    system.stop()

    print("Main wiring test completed.")


if __name__ == "__main__":
    main()
