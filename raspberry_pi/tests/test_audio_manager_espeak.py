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


class Result:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


def test_initialize_only_requires_espeak(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output=False, text=False, check=False):
        calls.append(cmd)
        return Result(returncode=0)

    monkeypatch.setattr("raspberry_pi.modules.audio_manager.subprocess.run", fake_run)

    audio = AudioManager({}, DummyLogger())

    assert audio.initialize() is True
    assert calls == [["which", "espeak"]]
    assert audio.initialized is True
    assert audio.mixer_initialized is True


def test_speak_uses_direct_espeak_command(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output=False, text=False, check=False):
        calls.append(cmd)
        return Result(returncode=0)

    monkeypatch.setattr("raspberry_pi.modules.audio_manager.subprocess.run", fake_run)

    audio = AudioManager(
        {"voice": "en-us", "speed": 120, "pitch": 55},
        DummyLogger()
    )
    audio.initialized = True
    audio.mixer_initialized = True

    audio.speak("hello world", wait=True)

    assert calls == [[
        "espeak",
        "-v", "en-us",
        "-s", "120",
        "-p", "55",
        "hello world",
    ]]


def test_speak_failure_disables_audio(monkeypatch):
    logger = DummyLogger()

    def fake_run(cmd, capture_output=False, text=False, check=False):
        return Result(returncode=1, stderr="audio backend failed")

    monkeypatch.setattr("raspberry_pi.modules.audio_manager.subprocess.run", fake_run)

    audio = AudioManager({}, logger)
    audio.initialized = True
    audio.mixer_initialized = True

    audio.speak("hello world", wait=True)

    assert audio.initialized is False
    assert audio.mixer_initialized is False
    assert ("error", "Unable to play audio: audio backend failed") in logger.messages
