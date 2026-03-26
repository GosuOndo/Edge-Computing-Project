"""Audio Manager - offline speech and alerts for Raspberry Pi"""

import queue
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
        self.output_device = config.get("output_device")

        self.initialized = False
        self.mixer_initialized = False

        # Serial playback queue – items are (text, done_event | None).
        # A single background worker thread drains the queue so that
        # simultaneous callers are played one-after-another rather than the
        # second one being dropped with "Audio busy".
        self._speech_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None

    def initialize(self):
        if not self.enabled:
            self.logger.info("Audio manager disabled")
            self.initialized = False
            self.mixer_initialized = False
            return True

        try:
            for tool_name in ["espeak", "aplay"]:
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

            # Start the single serial-playback worker thread.
            self._worker_thread = threading.Thread(
                target=self._queue_worker,
                name="audio-worker",
                daemon=True,
            )
            self._worker_thread.start()

            self.logger.info(
                "Audio manager initialized successfully using "
                f"espeak -> aplay "
                f"(voice={self.voice}, speed={self.speed}, pitch={self.pitch}, "
                f"device={self.output_device or 'default'})"
            )
            return True

        except Exception as e:
            self.logger.error(f"Audio init failed: {e}")
            self.initialized = False
            self.mixer_initialized = False
            return False

    # ------------------------------------------------------------------
    # Internal: worker thread + blocking espeak call
    # ------------------------------------------------------------------

    def _queue_worker(self):
        """Drain the speech queue serially; runs on the audio-worker thread."""
        while True:
            item = self._speech_queue.get()
            if item is None:          # sentinel – time to stop
                self._speech_queue.task_done()
                break
            text, done_event = item
            try:
                self._speak_blocking(text)
            finally:
                self._speech_queue.task_done()
                if done_event is not None:
                    done_event.set()

    def _speak_blocking(self, text: str):
        """Run espeak → aplay synchronously on the calling thread."""
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio speak skipped: audio not initialized")
            return

        safe_text = str(text).strip()
        if not safe_text:
            return

        self.logger.info(f"SPEAKING: {safe_text}")
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

        try:
            espeak_proc = subprocess.Popen(
                espeak_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            aplay_proc = subprocess.Popen(
                aplay_cmd,
                stdin=espeak_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=False,
            )

            if espeak_proc.stdout:
                espeak_proc.stdout.close()

            espeak_stderr = b""
            if espeak_proc.stderr:
                espeak_stderr = espeak_proc.stderr.read()
            aplay_stderr = aplay_proc.communicate()[1] or b""
            espeak_return = espeak_proc.wait()

            error_parts = []
            if espeak_stderr:
                error_parts.append(espeak_stderr.decode(errors="ignore").strip())
            if aplay_stderr:
                error_parts.append(aplay_stderr.decode(errors="ignore").strip())

            combined_rc = aplay_proc.returncode or espeak_return
            if combined_rc != 0:
                error_message = (
                    " | ".join(p for p in error_parts if p)
                    or "unknown audio backend error"
                )
                self.logger.error(f"Unable to play audio: {error_message}")
                self.initialized = False
                self.mixer_initialized = False

        except Exception as e:
            self.logger.error(f"Speak error: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def speak_async(self, text: str):
        """
        Enqueue *text* for playback and return immediately.
        If another message is already playing it will be played afterwards –
        nothing is dropped.
        """
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio speak skipped: audio not initialized")
            return
        self._speech_queue.put((str(text), None))

    def speak(self, text: str, wait: bool = True):
        """
        Speak *text*.  When *wait=True* (default) block until playback of
        this specific message completes.  When *wait=False* behave like
        speak_async.
        """
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio speak skipped: audio not initialized")
            return
        if wait:
            done = threading.Event()
            self._speech_queue.put((str(text), done))
            done.wait()
        else:
            self.speak_async(text)

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
        """Signal the worker thread to exit after finishing the current item."""
        self._speech_queue.put(None)   # sentinel

    def cleanup(self):
        self.stop()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)
        self.mixer_initialized = False
        self.logger.info("Audio manager cleanup complete")
