# recorder/audio.py
import queue
import threading
import time
from pathlib import Path

import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
BLOCK_SIZE = 1024


class AudioRecorder:
    """Records mic and system audio simultaneously to separate WAV files."""

    def __init__(
        self,
        mic_device: str,
        system_device: str,
        output_dir: Path,
        session_name: str,
    ):
        self.mic_device = mic_device
        self.system_device = system_device
        self.mic_path = output_dir / f"{session_name}-audio-mic.wav"
        self.system_path = output_dir / f"{session_name}-audio-system.wav"
        self._mic_q: queue.Queue = queue.Queue()
        self._sys_q: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._start_time: float | None = None
        self._mic_stream = None
        self._sys_stream = None
        self._mic_writer = None
        self._sys_writer = None
        self._write_error: str | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self._start_time = time.time()
        self._mic_stream = sd.InputStream(
            device=self.mic_device,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCK_SIZE,
            callback=lambda d, f, t, s: self._mic_q.put(d.copy()),
        )
        self._sys_stream = sd.InputStream(
            device=self.system_device,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCK_SIZE,
            callback=lambda d, f, t, s: self._sys_q.put(d.copy()),
        )
        self._mic_stream.start()
        self._sys_stream.start()
        self._mic_writer = threading.Thread(
            target=self._write_loop, args=(self._mic_q, self.mic_path), daemon=True
        )
        self._sys_writer = threading.Thread(
            target=self._write_loop, args=(self._sys_q, self.system_path), daemon=True
        )
        self._mic_writer.start()
        self._sys_writer.start()

    def stop(self) -> None:
        if self._mic_stream is None:
            return  # not started
        self._stop_event.set()
        self._mic_stream.stop()
        self._sys_stream.stop()
        self._mic_writer.join(timeout=5)
        if self._mic_writer.is_alive():
            raise RuntimeError("Mic writer thread did not finish within timeout — WAV file may be incomplete")
        self._sys_writer.join(timeout=5)
        if self._sys_writer.is_alive():
            raise RuntimeError("System writer thread did not finish within timeout — WAV file may be incomplete")
        if self._write_error:
            raise RuntimeError(f"Audio write failed: {self._write_error}")

    def elapsed_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def _write_loop(self, q: queue.Queue, path: Path) -> None:
        try:
            with sf.SoundFile(
                str(path), mode="w", samplerate=SAMPLE_RATE, channels=CHANNELS, subtype="PCM_16"
            ) as f:
                while not self._stop_event.is_set() or not q.empty():
                    try:
                        data = q.get(timeout=0.1)
                        f.write(data)
                    except queue.Empty:
                        continue
        except Exception as e:
            self._write_error = str(e)
