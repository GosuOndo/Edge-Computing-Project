"""Audio Manager - speech and alerts for Raspberry Pi"""

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
        # Kept for compatibility with older tests/scripts.
        self.mixer_initialized = False

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
                f"espeak (voice={self.voice}, speed={self.speed}, pitch={self.pitch})"
            )
            return True

        except Exception as e:
            self.logger.error(f"Audio init failed: {e}")
            self.initialized = False
            self.mixer_initialized = False
            return False

    def _build_espeak_command(self, text):
        return [
            "espeak",
            "-v", self.voice,
            "-s", str(self.speed),
            "-p", str(self.pitch),
            str(text).strip(),
        ]

    def _speak_worker(self, text):
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio speak skipped: audio not initialized")
            return

        safe_text = str(text).strip()
        if not safe_text:
            return

        try:
            cmd = self._build_espeak_command(safe_text)
            self.logger.info(f"SPEAKING: {safe_text}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                if stderr:
                    self.logger.error(f"Unable to play audio: {stderr}")
                else:
                    self.logger.error(
                        f"Unable to play audio: espeak exited with code {result.returncode}"
                    )
                self.initialized = False
                self.mixer_initialized = False

        except Exception as e:
            self.logger.error(f"Speak error: {e}")
            self.initialized = False
            self.mixer_initialized = False

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
        # kept for compatibility with existing code
        pass

    def stop(self):
        # kept for compatibility
        pass

    def cleanup(self):
        self.logger.info("Audio manager cleanup complete")
