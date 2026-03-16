import time

from raspberry_pi.main import MedicationSystem


def main():
    print("Starting UI/audio smoke test...")

    system = MedicationSystem(
        config_path="config/config.yaml",
        enable_display=True,
        enable_audio=True
    )

    try:
        print("Showing idle screen...")
        if system.display:
            system.display.show_idle_screen("08:00")
        time.sleep(3)

        print("Showing reminder screen...")
        if system.display:
            system.display.show_reminder_screen("Aspirin 100mg", 2, "08:00")
        if system.audio:
            system.audio.announce_reminder("Aspirin 100mg", 2)
        time.sleep(5)

        print("Showing monitoring screen...")
        if system.display:
            system.display.show_monitoring_screen(10, 30, "Monitoring intake...")
        time.sleep(5)

        print("Showing success screen...")
        if system.display:
            system.display.show_success_screen("Aspirin 100mg", "Medication taken successfully!")
        if system.audio:
            system.audio.announce_success("Aspirin 100mg")
        time.sleep(5)

        print("Showing warning screen...")
        if system.display:
            system.display.show_warning_screen("Incorrect Dosage", "Expected 2 pills, detected 1 pill")
        if system.audio:
            system.audio.announce_warning("Incorrect dosage detected")
        time.sleep(5)

        print("Returning to idle...")
        if system.display:
            system.display.show_idle_screen("20:00")
        time.sleep(3)

    except KeyboardInterrupt:
        print("\nStopped by user.")

    finally:
        system.stop()
        print("UI/audio smoke test completed.")


if __name__ == "__main__":
    main()
