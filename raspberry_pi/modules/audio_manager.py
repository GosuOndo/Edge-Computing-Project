"""Audio Manager - offline speech and alerts for Raspberry Pi"""

import subprocess
import threading
import shlex


class AudioManager:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

        self.enabled = config.get("enabled", True)
        self.voice = config.get("voice", "en-gb+f3")
        self.speed = int(config.get("speed", 85))
        self.pitch = int(config.get("pitch", 45))

        self.initialized = False
        # Non-blocking lock: if audio is already playing, new requests are
        # dropped instead of queuing up and fighting over the ALSA device.
        self._audio_lock = threading.Lock()

    def initialize(self):
        if not self.enabled:
            self.logger.info("Audio manager disabled")
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
                return False

            self.initialized = True
            self.logger.info(
                f"Audio manager initialized (voice={self.voice}, "
                f"speed={self.speed}, pitch={self.pitch})"
            )
            return True

        except Exception as e:
            self.logger.error(f"Audio init failed: {e}")
            self.initialized = False
            return False

    def _speak_worker(self, text):
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio speak skipped: audio not initialized")
            return

        # If audio is already playing, skip this request rather than pile up
        # threads that all compete for the ALSA device (causes error 524 /
        # broken pipe on the Raspberry Pi).
        if not self._audio_lock.acquire(blocking=False):
            self.logger.debug(f"Audio busy, skipping: {text}")
            return

        try:
            safe_text = str(text).strip()
            if not safe_text:
                return

            quoted_text = shlex.quote(safe_text)

            # Use espeak's built-in audio output directly.
            # The old pipeline (espeak --stdout | ffmpeg | aplay) caused
            # "aplay: Unknown error 524" (ALSA ESTRPIPE) because aplay
            # could not open the device when PulseAudio/PipeWire is running
            # or the ALSA stream is suspended. espeak without --stdout plays
            # through its own PortAudio backend which handles all of this
            # automatically. -a 200 sets amplitude to maximum.
            cmd = (
                f"espeak -v {self.voice} -s {self.speed} "
                f"-p {self.pitch} -a 200 {quoted_text}"
            )

            self.logger.info(f"SPEAKING: {safe_text}")
            subprocess.run(cmd, shell=True, check=False)

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
        self.logger.info("Audio manager cleanup complete")
