"""Audio Manager - offline speech and alerts for Raspberry Pi"""

import subprocess
import threading
import os


class AudioManager:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

        self.enabled = config.get("enabled", True)
        self.voice = config.get("voice", "en-gb+f3")
        self.speed = int(config.get("speed", 85))
        self.pitch = int(config.get("pitch", 45))
        self.playback_mode = config.get("playback_mode", "alsa_pipe")
        self.output_device = config.get("output_device")

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
            required_tools = ["espeak"]
            if self.playback_mode == "alsa_pipe":
                required_tools.append("aplay")

            for tool_name in required_tools:
                result = subprocess.run(
                    ["which", tool_name],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode != 0:
                    self.logger.error(f"Audio init failed: {tool_name} not found")
                    self.initialized = False
                    self.mixer_initialized = False
                    return False

            self.initialized = True
            self.mixer_initialized = True
            self.logger.info(
                "Audio manager initialized successfully using "
                f"{self.playback_mode} "
                f"(voice={self.voice}, speed={self.speed}, pitch={self.pitch}, "
                f"device={self.output_device or 'default'})"
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
            result = self._play_text(safe_text)
            if result.returncode != 0:
                error_message = result.stderr.strip() or "unknown audio backend error"
                self.logger.error(f"Unable to play audio: {error_message}")
                self.initialized = False
                self.mixer_initialized = False

        except Exception as e:
            self.logger.error(f"Speak error: {e}")
        finally:
            self._audio_lock.release()

    def _play_text(self, safe_text):
        if self.playback_mode == "direct_espeak":
            return subprocess.run(
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

        espeak_cmd = [
            "espeak",
            "-v", str(self.voice),
            "-s", str(self.speed),
            "-p", str(self.pitch),
            "--stdout",
            safe_text,
        ]
        aplay_cmd = ["aplay", "-q"]
        if self.output_device:
            aplay_cmd.extend(["-D", str(self.output_device)])

        env = dict(os.environ)
        if self.output_device:
            env["AUDIODEV"] = str(self.output_device)

        espeak_proc = subprocess.Popen(
            espeak_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        aplay_proc = subprocess.Popen(
            aplay_cmd,
            stdin=espeak_proc.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )

        if espeak_proc.stdout:
            espeak_proc.stdout.close()

        espeak_stderr = espeak_proc.stderr.read() if espeak_proc.stderr else b""
        aplay_stderr = aplay_proc.communicate()[1] or b""
        espeak_return = espeak_proc.wait()

        stderr_parts = []
        if espeak_stderr:
            stderr_parts.append(espeak_stderr.decode(errors="ignore").strip())
        if aplay_stderr:
            stderr_parts.append(aplay_stderr.decode(errors="ignore").strip())

        class Result:
            def __init__(self, returncode=0, stderr=""):
                self.returncode = returncode
                self.stderr = stderr

        return Result(
            returncode=aplay_proc.returncode or espeak_return,
            stderr=" | ".join(part for part in stderr_parts if part),
        )

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
