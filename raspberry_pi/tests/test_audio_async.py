from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.audio_manager import AudioManager
import time

def main():
    print("Starting async audio test...")

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

    audio.speak_async("This is an asynchronous audio test.")
    print("Async speech started.")
    time.sleep(5)

    audio.cleanup()
    print("Async audio test completed.")

if __name__ == "__main__":
    main()
