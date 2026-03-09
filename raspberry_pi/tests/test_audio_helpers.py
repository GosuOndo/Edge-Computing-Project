from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.audio_manager import AudioManager
import time

def main():
    print("Starting audio helper methods test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    audio_config = {
        "enabled": True,
        "volume": 0.8
    }

    audio = AudioManager(audio_config, logger)

    if not audio.initialize():
        print("Audio initialization failed.")
        return

    print("Testing reminder...")
    audio.announce_reminder("Panadol", 2)
    time.sleep(1)

    print("Testing success...")
    audio.announce_success("Panadol")
    time.sleep(1)

    print("Testing warning...")
    audio.announce_warning("Please verify your dosage.")
    time.sleep(1)

    audio.cleanup()
    print("Audio helper methods test completed.")

if __name__ == "__main__":
    main()
