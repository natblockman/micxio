from __future__ import annotations

import queue
import threading
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class RecordingError(RuntimeError):
    pass


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str
    sample_rate: int
    channels: int


def _sounddevice():
    try:
        import sounddevice as sd  # type: ignore

        return sd
    except (ImportError, OSError) as error:
        raise RecordingError(f"Audio input system is unavailable: {error}") from error


def list_input_devices() -> list[InputDevice]:
    sd = _sounddevice()
    try:
        raw_devices = sd.query_devices()
    except Exception as error:
        raise RecordingError(str(error)) from error
    devices = [
        InputDevice(
            index=index,
            name=str(info["name"]),
            sample_rate=max(8000, int(round(float(info["default_samplerate"])))),
            channels=int(info["max_input_channels"]),
        )
        for index, info in enumerate(raw_devices)
        if int(info["max_input_channels"]) > 0
    ]
    try:
        default_index = int(sd.default.device[0])
        devices.sort(key=lambda device: device.index != default_index)
    except (TypeError, ValueError, IndexError):
        pass
    return devices


def list_output_devices() -> list[OutputDevice]:
    sd = _sounddevice()
    try:
        raw_devices = sd.query_devices()
    except Exception as error:
        raise RecordingError(str(error)) from error
    devices = [
        OutputDevice(
            index=index,
            name=str(info["name"]),
            sample_rate=max(8000, int(round(float(info["default_samplerate"])))),
            channels=int(info["max_output_channels"]),
        )
        for index, info in enumerate(raw_devices)
        if int(info["max_output_channels"]) > 0
    ]
    try:
        default_index = int(sd.default.device[1])
        devices.sort(key=lambda device: device.index != default_index)
    except (TypeError, ValueError, IndexError):
        pass
    return devices


class MicrophoneRecorder:
    """Record mono 16-bit PCM from a microphone without blocking the UI."""

    def __init__(
        self,
        device: InputDevice,
        on_level: Callable[[float], None] | None = None,
    ) -> None:
        self.device = device
        self.on_level = on_level
        self._stream = None
        self._frames: queue.Queue[bytes | None] = queue.Queue()
        self._writer: threading.Thread | None = None
        self._path: Path | None = None
        self._frame_count = 0
        self._writer_error: Exception | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, path: Path) -> None:
        if self._active:
            raise RecordingError("Recording is already active")
        sd = _sounddevice()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._frame_count = 0
        self._writer_error = None
        self._writer = threading.Thread(target=self._write_wave, daemon=True)
        self._writer.start()

        def callback(indata, frames, _time_info, status) -> None:
            if status:
                # PortAudio status flags are non-fatal; the stream can continue.
                pass
            chunk = bytes(indata)
            self._frames.put(chunk)
            self._frame_count += frames
            if self.on_level:
                samples = array("h")
                samples.frombytes(chunk)
                peak = max((abs(sample) for sample in samples), default=0) / 32768.0
                self.on_level(min(1.0, peak))

        try:
            self._stream = sd.RawInputStream(
                samplerate=self.device.sample_rate,
                blocksize=1024,
                device=self.device.index,
                channels=1,
                dtype="int16",
                callback=callback,
            )
            self._stream.start()
            self._active = True
        except Exception as error:
            self._frames.put(None)
            if self._writer:
                self._writer.join(timeout=3)
            path.unlink(missing_ok=True)
            raise RecordingError(str(error)) from error

    def stop(self) -> Path:
        if not self._path:
            raise RecordingError("Recording has not started")
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as error:
                self._writer_error = self._writer_error or error
            finally:
                self._stream = None
        self._active = False
        self._frames.put(None)
        if self._writer:
            self._writer.join(timeout=5)
        if self._writer and self._writer.is_alive():
            raise RecordingError("Timed out while saving the recording")
        if self._writer_error:
            self._path.unlink(missing_ok=True)
            raise RecordingError(str(self._writer_error))
        if self._frame_count == 0 or not self._path.exists():
            self._path.unlink(missing_ok=True)
            raise RecordingError("The microphone did not provide any audio")
        return self._path

    def _write_wave(self) -> None:
        assert self._path is not None
        try:
            with wave.open(str(self._path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(self.device.sample_rate)
                while True:
                    chunk = self._frames.get()
                    if chunk is None:
                        break
                    output.writeframesraw(chunk)
        except Exception as error:
            self._writer_error = error
