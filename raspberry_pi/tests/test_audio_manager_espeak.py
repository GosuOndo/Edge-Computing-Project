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


def test_initialize_requires_espeak_and_aplay(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output=False, text=False, check=False):
        calls.append(cmd)
        return Result(returncode=0)

    monkeypatch.setattr("raspberry_pi.modules.audio_manager.subprocess.run", fake_run)

    audio = AudioManager({}, DummyLogger())

    assert audio.initialize() is True
    assert calls == [["which", "espeak"], ["which", "aplay"]]
    assert audio.initialized is True
    assert audio.mixer_initialized is True


def test_speak_uses_espeak_stdout_with_aplay_device(monkeypatch):
    popen_calls = []

    class FakeStdout:
        def close(self):
            pass

    class FakeStderr:
        def __init__(self, payload=b""):
            self.payload = payload

        def read(self):
            return self.payload

    class FakePopen:
        def __init__(self, cmd, stdout=None, stderr=None, stdin=None, text=None):
            popen_calls.append(cmd)
            self.cmd = cmd
            self.stdout = FakeStdout() if cmd[0] == "espeak" else None
            self.stderr = FakeStderr()
            self.returncode = 0

        def communicate(self):
            return (b"", b"")

        def wait(self):
            return 0

    monkeypatch.setattr("raspberry_pi.modules.audio_manager.subprocess.Popen", FakePopen)

    audio = AudioManager(
        {
            "voice": "en-us",
            "speed": 120,
            "pitch": 55,
            "output_device": "plughw:CARD=vc4hdmi0,DEV=0",
        },
        DummyLogger()
    )
    audio.initialized = True
    audio.mixer_initialized = True

    audio.speak("hello world", wait=True)

    assert popen_calls[0] == [
        "espeak",
        "-v", "en-us",
        "-s", "120",
        "-p", "55",
        "--stdout",
        "hello world",
    ]
    assert popen_calls[1] == [
        "aplay",
        "-q",
        "-D",
        "plughw:CARD=vc4hdmi0,DEV=0",
    ]


def test_speak_failure_disables_audio(monkeypatch):
    logger = DummyLogger()

    class FakeStdout:
        def close(self):
            pass

    class FakeStderr:
        def __init__(self, payload=b""):
            self.payload = payload

        def read(self):
            return self.payload

    class FakePopen:
        def __init__(self, cmd, stdout=None, stderr=None, stdin=None, text=None):
            self.cmd = cmd
            self.stdout = FakeStdout() if cmd[0] == "espeak" else None
            self.stderr = FakeStderr()
            self.returncode = 1 if cmd[0] == "aplay" else 0

        def communicate(self):
            if self.cmd[0] == "aplay":
                return (b"", b"audio backend failed")
            return (b"", b"")

        def wait(self):
            return 0

    monkeypatch.setattr("raspberry_pi.modules.audio_manager.subprocess.Popen", FakePopen)

    audio = AudioManager({}, logger)
    audio.initialized = True
    audio.mixer_initialized = True

    audio.speak("hello world", wait=True)

    assert audio.initialized is False
    assert audio.mixer_initialized is False
    assert ("error", "Unable to play audio: audio backend failed") in logger.messages
