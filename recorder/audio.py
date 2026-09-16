# recorder/audio.py
import queue
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
BLOCK_SIZE = 1024

# RMS threshold below which mic frames are replaced with silence (backwash filter).
# int16 range is 0–32767; 300 ≈ ~0.9% of max, catches breath/bleed without clipping speech.
MIC_SILENCE_THRESHOLD = 300


class AudioRecorder:
    """Records mic and system audio simultaneously to separate WAV files."""

    def __init__(
        self,
        mic_device: str,
        system_device: str,
        output_dir: Path,
        session_name: str,
        mic_threshold: int = MIC_SILENCE_THRESHOLD,
        capture_method: str = "auto",
    ):
        self.mic_device = mic_device
        self.system_device = system_device
        self.mic_path = output_dir / f"{session_name}-audio-mic.wav"
        self.system_path = output_dir / f"{session_name}-audio-system.wav"
        self._mic_threshold = mic_threshold
        self._capture_method = capture_method
        # Which path actually captured system audio: "tap" or "blackhole".
        # Set during start() and reported afterwards so the UI can say so.
        self.system_capture_method: str | None = None
        # Why the tap was not used, when it was wanted but unavailable.
        self.tap_error: str | None = None
        self._tap = None
        self._sys_frames = 0
        self._sys_sq_sum = 0.0
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
            callback=self._mic_callback,
        )
        self._start_system_capture()
        self._mic_stream.start()
        if self._sys_stream is not None:
            self._sys_stream.start()
        self._mic_writer = threading.Thread(
            target=self._write_loop, args=(self._mic_q, self.mic_path), daemon=True
        )
        self._sys_writer = threading.Thread(
            target=self._write_loop, args=(self._sys_q, self.system_path), daemon=True
        )
        self._mic_writer.start()
        self._sys_writer.start()

    def _start_system_capture(self) -> None:
        """Capture system audio with a process tap, or fall back to a loopback
        input device.

        The tap needs no setup from the user and is unaffected by which output
        device they are listening on, so it is preferred whenever available.
        ``capture_method`` pins the choice for troubleshooting.
        """
        if self._capture_method != "blackhole":
            try:
                from recorder.systemtap import SystemAudioTap, SystemTapUnavailable, to_mono_16k

                def on_block(samples, rate, channels):
                    # Real-time thread: convert and enqueue, nothing heavier.
                    self._sys_q.put(to_mono_16k(samples, rate, channels))

                tap = SystemAudioTap(on_block)
                tap.start()
                self._tap = tap
                self.system_capture_method = "tap"
                return
            except SystemTapUnavailable as e:
                self.tap_error = str(e)
                if self._capture_method == "tap":
                    raise
            except Exception as e:  # import failure, unexpected binding error
                self.tap_error = str(e)
                if self._capture_method == "tap":
                    raise

        self._sys_stream = sd.InputStream(
            device=self.system_device,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCK_SIZE,
            callback=lambda d, f, t, s: self._sys_q.put(d.copy()),
        )
        self.system_capture_method = "blackhole"

    def stop(self) -> None:
        if self._mic_stream is None:
            return  # not started
        self._stop_event.set()
        self._mic_stream.stop()
        if self._tap is not None:
            self._tap.stop()
        if self._sys_stream is not None:
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

    def system_rms(self) -> float:
        """RMS of the system track that was written, 0.0 when nothing was.

        System capture fails silently — a denied tap and an unrouted loopback
        both yield a well-formed file full of zeroes — so callers check this
        rather than assuming a quiet result means a quiet meeting.
        """
        try:
            import soundfile as sf_read
            if not self.system_path.exists():
                return 0.0
            data, _ = sf_read.read(str(self.system_path), dtype="float32")
            if data.size == 0:
                return 0.0
            return float(np.sqrt(np.mean(np.square(data))))
        except Exception:
            return 0.0

    def _mic_callback(self, data, frames, time_info, status) -> None:
        """Gate mic frames: replace with silence if RMS is below threshold."""
        rms = np.sqrt(np.mean(data.astype(np.float32) ** 2))
        if rms >= self._mic_threshold:
            self._mic_q.put(data.copy())
        else:
            self._mic_q.put(np.zeros_like(data))

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

