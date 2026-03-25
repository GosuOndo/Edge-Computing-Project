"""Audio Manager - native sounddevice cue playback for Raspberry Pi.

This replaces the old shell-based espeak pipeline with a small Python-native
audio backend inspired by the sounddevice workflow used in the
INF2009_SoundAnalytics lab. The system now emits distinct cue patterns for
reminders, confirmations, warnings, and general prompts without depending on
external CLI tools.
"""

import threading

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - handled gracefully at runtime
    sd = None


class AudioManager:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

        self.enabled = config.get("enabled", True)
        self.backend = config.get("backend", "sounddevice")
        self.volume = float(config.get("volume", 0.8))
        self.sample_rate = int(config.get("sample_rate", 16000))
        self.base_frequency_hz = float(config.get("base_frequency_hz", 523.25))
        self.cue_gap_seconds = float(config.get("cue_gap_seconds", 0.04))

        self.initialized = False
        self.mixer_initialized = False
        self._audio_lock = threading.Lock()

        self._cue_profiles = {
            "generic": [
                (1.00, 0.11, 0.45),
                (1.25, 0.11, 0.35),
            ],
            "reading": [
                (1.15, 0.08, 0.30),
                (1.35, 0.08, 0.30),
                (1.55, 0.08, 0.30),
            ],
            "registration": [
                (1.00, 0.10, 0.35),
                (1.20, 0.10, 0.35),
                (1.50, 0.16, 0.40),
            ],
            "reminder": [
                (1.00, 0.12, 0.42),
                (1.26, 0.12, 0.42),
                (1.50, 0.18, 0.46),
                (1.26, 0.10, 0.34),
            ],
            "success": [
                (1.00, 0.08, 0.34),
                (1.26, 0.08, 0.34),
                (1.50, 0.12, 0.38),
                (2.00, 0.22, 0.36),
            ],
            "warning": [
                (0.95, 0.12, 0.48),
                (0.75, 0.12, 0.50),
                (0.95, 0.16, 0.52),
            ],
            "error": [
                (0.90, 0.16, 0.55),
                (0.70, 0.16, 0.58),
                (0.55, 0.20, 0.62),
            ],
        }

    def initialize(self):
        if not self.enabled:
            self.logger.info("Audio manager disabled")
            self.initialized = False
            self.mixer_initialized = False
            return True

        if self.backend != "sounddevice":
            self.logger.error(
                f"Audio init failed: unsupported backend '{self.backend}'"
            )
            self.initialized = False
            self.mixer_initialized = False
            return False

        if sd is None:
            self.logger.error("Audio init failed: sounddevice not available")
            self.initialized = False
            self.mixer_initialized = False
            return False

        try:
            sd.query_devices()
            self.initialized = True
            self.mixer_initialized = True
            self.logger.info(
                "Audio manager initialized successfully using sounddevice "
                f"(sample_rate={self.sample_rate}, volume={self.volume:.2f})"
            )
            return True

        except Exception as e:
            self.logger.error(f"Audio init failed: {e}")
            self.initialized = False
            self.mixer_initialized = False
            return False

    def _classify_text(self, text):
        lowered = str(text).lower()

        if any(token in lowered for token in ["wrong", "warning", "missed", "error", "failed"]):
            return "warning"
        if any(token in lowered for token in ["success", "successfully", "thank you", "confirmed"]):
            return "success"
        if any(token in lowered for token in ["register", "registration", "place medicine"]):
            return "registration"
        if "reading tag" in lowered or "reading" in lowered:
            return "reading"
        if any(token in lowered for token in ["time to take", "medication reminder", "reminder"]):
            return "reminder"
        return "generic"

    def _fade_envelope(self, length):
        if length <= 1:
            return np.ones(length, dtype=np.float32)

        fade_len = max(1, min(length // 6, int(self.sample_rate * 0.01)))
        envelope = np.ones(length, dtype=np.float32)
        fade = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
        envelope[:fade_len] = fade
        envelope[-fade_len:] = fade[::-1]
        return envelope

    def _tone(self, multiplier, duration_seconds, amplitude):
        sample_count = max(1, int(self.sample_rate * duration_seconds))
        timeline = np.linspace(
            0.0,
            duration_seconds,
            sample_count,
            endpoint=False,
            dtype=np.float32
        )
        frequency = self.base_frequency_hz * float(multiplier)
        waveform = np.sin(2 * np.pi * frequency * timeline).astype(np.float32)
        waveform *= self._fade_envelope(sample_count)
        waveform *= float(amplitude) * self.volume
        return waveform.astype(np.float32)

    def _silence(self, duration_seconds):
        sample_count = max(1, int(self.sample_rate * duration_seconds))
        return np.zeros(sample_count, dtype=np.float32)

    def _build_cue(self, cue_name):
        profile = self._cue_profiles.get(cue_name, self._cue_profiles["generic"])
        parts = []

        for index, (multiplier, duration_seconds, amplitude) in enumerate(profile):
            if index > 0:
                parts.append(self._silence(self.cue_gap_seconds))
            parts.append(self._tone(multiplier, duration_seconds, amplitude))

        if not parts:
            return self._silence(0.1)

        return np.concatenate(parts).astype(np.float32)

    def _play_cue(self, cue_name, context_text):
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio playback skipped: audio not initialized")
            return

        if not self._audio_lock.acquire(blocking=False):
            self.logger.info(f"Audio busy, skipping cue: {context_text}")
            return

        try:
            waveform = self._build_cue(cue_name)
            self.logger.info(f"PLAYING {cue_name} cue: {context_text}")
            sd.play(waveform, self.sample_rate, blocking=False)
            sd.wait()
        except Exception as e:
            self.logger.error(f"Unable to play audio: {e}")
            self.initialized = False
            self.mixer_initialized = False
        finally:
            self._audio_lock.release()

    def _play_text(self, text):
        safe_text = str(text).strip()
        if not safe_text:
            return
        cue_name = self._classify_text(safe_text)
        self._play_cue(cue_name, safe_text)

    def speak(self, text, wait=True):
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio speak skipped: audio not initialized")
            return

        if wait:
            self._play_text(text)
        else:
            threading.Thread(
                target=self._play_text,
                args=(text,),
                daemon=True
            ).start()

    def speak_async(self, text):
        self.speak(text, wait=False)

    def announce_reminder(self, medicine_name, dosage):
        self._announce_with_cue(
            "reminder",
            f"Reminder for {medicine_name}: take {dosage} pill(s)."
        )

    def announce_success(self, medicine_name):
        self._announce_with_cue(
            "success",
            f"Success for {medicine_name}."
        )

    def announce_warning(self, message):
        self._announce_with_cue(
            "warning",
            f"Warning: {message}"
        )

    def _announce_with_cue(self, cue_name, context_text):
        if not self.initialized or not self.enabled:
            self.logger.warning("Audio playback skipped: audio not initialized")
            return

        threading.Thread(
            target=self._play_cue,
            args=(cue_name, context_text),
            daemon=True
        ).start()

    def set_volume(self, volume):
        self.volume = max(0.0, min(1.0, float(volume)))

    def stop(self):
        if sd is None:
            return
        try:
            sd.stop()
        except Exception as e:
            self.logger.warning(f"Audio stop warning: {e}")

    def cleanup(self):
        self.stop()
        self.logger.info("Audio manager cleanup complete")
