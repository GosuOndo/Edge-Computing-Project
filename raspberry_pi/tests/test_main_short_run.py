import threading
import time
from raspberry_pi.main import MedicationSystem


def main():
    print("Starting short main loop test...")

    system = MedicationSystem(config_path="config/config.yaml")

    thread = threading.Thread(target=system.start, daemon=True)
    thread.start()

    time.sleep(5)

    system.stop()
    thread.join(timeout=5)

    print("Short main loop test completed.")


if __name__ == "__main__":
    main()
