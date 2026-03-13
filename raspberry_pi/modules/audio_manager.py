"""Audio Manager - TTS and alerts"""
import os
os.environ["SDL_AUDIODRIVER"] = "alsa"

import time
from pathlib import Path
from threading import Thread, Lock

import pygame
import pygame.mixer
from gtts import gTTS


class AudioManager:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.enabled = config.get('enabled', True)
        self.volume = config.get('volume', 0.8)
        self.cache_dir = Path('data/audio_cache')
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.mixer_initialized = False
        self.audio_lock = Lock()
        self.cached_messages = {}

    def initialize(self):
        if not self.enabled:
            self.logger.info("Audio manager disabled")
            return True

        try:
            pygame.mixer.init()
            pygame.mixer.music.set_volume(self.volume)
            self.mixer_initialized = True
            self.logger.info("Audio mixer initialized successfully using ALSA")
            return True
        except Exception as e:
            self.mixer_initialized = False
            self.logger.error(f"Audio init failed: {e}")
            return False

    def _get_audio_file(self, text, lang='en'):
        key = f"{text}_{lang}"

        if key in self.cached_messages:
            return self.cached_messages[key]

        filepath = self.cache_dir / f"{hash(key)}.mp3"
        if filepath.exists():
            self.cached_messages[key] = str(filepath)
            return str(filepath)

        try:
            tts = gTTS(text=text, lang=lang, slow=False)
            tts.save(str(filepath))
            self.cached_messages[key] = str(filepath)
            return str(filepath)
        except Exception as e:
            self.logger.error(f"TTS generation failed: {e}")
            return None

    def speak(self, text, wait=True):
        if not self.enabled:
            return

        if not self.mixer_initialized or not pygame.mixer.get_init():
            self.logger.warning("Audio speak skipped: mixer not initialized")
            return

        with self.audio_lock:
            try:
                audio_file = self._get_audio_file(text)
                if audio_file:
                    pygame.mixer.music.load(audio_file)
                    pygame.mixer.music.play()

                    if wait:
                        while pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                            time.sleep(0.1)

            except Exception as e:
                self.logger.error(f"Speak error: {e}")

    def speak_async(self, text):
        Thread(target=self.speak, args=(text, True), daemon=True).start()

    def announce_reminder(self, medicine_name, dosage):
        self.speak(f"Time to take your medication. {medicine_name}, {dosage} pills.")

    def announce_success(self, medicine_name):
        self.speak(f"Thank you. {medicine_name} taken successfully.")

    def announce_warning(self, message):
        self.speak(f"Warning. {message}")

    def set_volume(self, volume):
        self.volume = max(0.0, min(1.0, volume))

        try:
            if self.mixer_initialized and pygame.mixer.get_init():
                pygame.mixer.music.set_volume(self.volume)
        except Exception as e:
            self.logger.warning(f"Set volume warning: {e}")

    def stop(self):
        try:
            if self.mixer_initialized and pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception as e:
            self.logger.warning(f"Audio stop warning: {e}")

    def cleanup(self):
        try:
            self.stop()

            if self.mixer_initialized and pygame.mixer.get_init():
                pygame.mixer.quit()

        except Exception as e:
            self.logger.warning(f"Audio cleanup warning: {e}")

        finally:
            self.mixer_initialized = False
            self.logger.info("Audio manager cleanup complete")
