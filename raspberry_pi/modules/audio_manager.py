"""Audio Manager - TTS and alerts"""
import os
os.environ["SDL_AUDIODRIVER"] = "alsa"

import pygame.mixer
from gtts import gTTS
import time
from pathlib import Path
from threading import Thread, Lock

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
            return True
        try:
            pygame.mixer.init()
            pygame.mixer.music.set_volume(self.volume)
            self.mixer_initialized = True
            self.logger.info("Audio mixer initialized successfully using ALSA")
            return True
        except Exception as e:
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
        except:
            return None
    
    def speak(self, text, wait=True):
        if not self.enabled or not self.mixer_initialized:
            return
        with self.audio_lock:
            try:
                audio_file = self._get_audio_file(text)
                if audio_file:
                    pygame.mixer.music.load(audio_file)
                    pygame.mixer.music.play()
                    if wait:
                        while pygame.mixer.music.get_busy():
                            time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"Speak error: {e}")
    
    def speak_async(self, text):
        Thread(target=self.speak, args=(text,True), daemon=True).start()
    
    def announce_reminder(self, medicine_name, dosage):
        self.speak(f"Time to take your medication. {medicine_name}, {dosage} pills.")
    
    def announce_success(self, medicine_name):
        self.speak(f"Thank you. {medicine_name} taken successfully.")
    
    def announce_warning(self, message):
        self.speak(f"Warning. {message}")
    
    def set_volume(self, volume):
        self.volume = max(0.0, min(1.0, volume))
        if self.mixer_initialized:
            pygame.mixer.music.set_volume(self.volume)
    
    def stop(self):
        if self.mixer_initialized:
            pygame.mixer.music.stop()
    
    def cleanup(self):
        self.stop()
        if self.mixer_initialized:
            pygame.mixer.quit()
            self.mixer_initialized = False
