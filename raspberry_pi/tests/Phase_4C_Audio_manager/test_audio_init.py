from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.audio_manager import AudioManager

def main():
    print("Starting audio init test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    audio_config = {
        "enabled": True,
        "volume": 0.8
    }

    audio = AudioManager(audio_config, logger)
    success = audio.initialize()

    print("Audio initialized:", success)
    print("Mixer initialized:", audio.mixer_initialized)

    audio.cleanup()
    print("Audio init test completed.")

if __name__ == "__main__":
    main()
