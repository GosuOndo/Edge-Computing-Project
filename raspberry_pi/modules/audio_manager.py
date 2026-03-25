"""Audio Manager - offline speech and alerts for Raspberry Pi"""

import subprocess
import threading


class AudioManager:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

        self.enabled = config.get("enabled", True)
        self.voice = config.get("voice", "en-gb+f3")
        self.speed = int(config.get("speed", 85))
        self.pitch = int(config.get("pitch", 45))

        self.initialized = False
        self.mixer_initialized = False
        self._audio_lock = threading.Lock()

    def initialize(self):
        if not self.enabled:
            self.logger.info("Audio manager disabled")
            self.initialized = False
            self.mixer_initialized = False
            return True

        try:
            result = subprocess.run(
                ["which", "espeak"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                self.logger.error("Audio init failed: espeak not found")
                self.initialized = False
                self.mixer_initialized = False
                return False

            self.initialized = True
            self.mixer_initialized = True
            self.logger.info(
                "Audio manager initialized successfully using "
                f"direct espeak playback "
                f"(voice={self.voice}, speed={self.speed}, pitch={self.pitch})"
            )
            return True

        except Exception as e:
            self.logger.error(f"Audio init failed: {e}")
            self.initialized = False
            self.mixer_initialized = False
            return False

    def _speak_worker(self, text):
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio speak skipped: audio not initialized")
            return

        if not self._audio_lock.acquire(blocking=False):
            self.logger.info(f"Audio busy, skipping: {text}")
            return

        try:
            safe_text = str(text).strip()
            if not safe_text:
                return

            self.logger.info(f"SPEAKING: {safe_text}")
            result = subprocess.run(
                [
                    "espeak",
                    "-v", str(self.voice),
                    "-s", str(self.speed),
                    "-p", str(self.pitch),
                    safe_text,
                ],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                error_message = result.stderr.strip() or "unknown audio backend error"
                self.logger.error(f"Unable to play audio: {error_message}")
                self.initialized = False
                self.mixer_initialized = False

        except Exception as e:
            self.logger.error(f"Speak error: {e}")
        finally:
            self._audio_lock.release()

    def speak(self, text, wait=True):
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio speak skipped: audio not initialized")
            return

        if wait:
            self._speak_worker(text)
        else:
            threading.Thread(
                target=self._speak_worker,
                args=(text,),
                daemon=True
            ).start()

    def speak_async(self, text):
        self.speak(text, wait=False)

    def announce_reminder(self, medicine_name, dosage):
        self.speak_async(
            f"Time to take your medication. {medicine_name}. {dosage} pills."
        )

    def announce_success(self, medicine_name):
        self.speak_async(
            f"Thank you. {medicine_name} taken successfully."
        )

    def announce_warning(self, message):
        self.speak_async(f"Warning. {message}")

    def set_volume(self, volume):
        pass

    def stop(self):
        pass

    def cleanup(self):
        self.mixer_initialized = False
        self.logger.info("Audio manager cleanup complete")
