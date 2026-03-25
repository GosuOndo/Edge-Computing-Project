from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from raspberry_pi.modules.audio_manager import AudioManager


class DummyLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))


class FakeSoundDevice:
    def __init__(self):
        self.play_calls = []
        self.wait_calls = 0
        self.stop_calls = 0
        self.query_calls = 0

    def query_devices(self):
        self.query_calls += 1
        return [{"name": "fake-output"}]

    def play(self, waveform, sample_rate, blocking=False):
        self.play_calls.append((waveform, sample_rate, blocking))

    def wait(self):
        self.wait_calls += 1

    def stop(self):
        self.stop_calls += 1


def test_initialize_uses_sounddevice_backend(monkeypatch):
    fake_sd = FakeSoundDevice()
    monkeypatch.setattr("raspberry_pi.modules.audio_manager.sd", fake_sd)

    audio = AudioManager({}, DummyLogger())

    assert audio.initialize() is True
    assert fake_sd.query_calls == 1
    assert audio.initialized is True
    assert audio.mixer_initialized is True


def test_speak_plays_native_waveform(monkeypatch):
    fake_sd = FakeSoundDevice()
    monkeypatch.setattr("raspberry_pi.modules.audio_manager.sd", fake_sd)

    audio = AudioManager({"sample_rate": 8000, "volume": 0.5}, DummyLogger())
    audio.initialized = True
    audio.mixer_initialized = True

    audio.speak("hello world", wait=True)

    assert len(fake_sd.play_calls) == 1
    waveform, sample_rate, blocking = fake_sd.play_calls[0]
    assert sample_rate == 8000
    assert blocking is False
    assert waveform.ndim == 1
    assert waveform.size > 0
    assert fake_sd.wait_calls == 1


def test_speak_failure_disables_audio(monkeypatch):
    logger = DummyLogger()

    class BrokenSoundDevice(FakeSoundDevice):
        def play(self, waveform, sample_rate, blocking=False):
            raise RuntimeError("audio backend failed")

    monkeypatch.setattr("raspberry_pi.modules.audio_manager.sd", BrokenSoundDevice())

    audio = AudioManager({}, logger)
    audio.initialized = True
    audio.mixer_initialized = True

    audio.speak("hello world", wait=True)

    assert audio.initialized is False
    assert audio.mixer_initialized is False
    assert ("error", "Unable to play audio: audio backend failed") in logger.messages
