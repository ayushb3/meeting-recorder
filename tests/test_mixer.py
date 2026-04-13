import numpy as np
import tempfile, os
from pathlib import Path
from scipy.io import wavfile


def make_wav(path: Path, data: np.ndarray, rate: int = 16000):
    wavfile.write(str(path), rate, data.astype(np.int16))


def test_mix_produces_average():
    from recorder.mixer import mix_wavs
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = np.array([1000, 2000, 3000], dtype=np.int16)
        b = np.array([3000, 2000, 1000], dtype=np.int16)
        make_wav(d / "mic.wav", a)
        make_wav(d / "sys.wav", b)
        out = d / "mixed.wav"
        mix_wavs(d / "mic.wav", d / "sys.wav", out)
        rate, data = wavfile.read(str(out))
        assert rate == 16000
        np.testing.assert_array_equal(data, np.array([2000, 2000, 2000], dtype=np.int16))


def test_mix_handles_length_mismatch():
    from recorder.mixer import mix_wavs
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = np.array([1000, 2000, 3000, 4000], dtype=np.int16)
        b = np.array([2000, 2000], dtype=np.int16)
        make_wav(d / "mic.wav", a)
        make_wav(d / "sys.wav", b)
        out = d / "mixed.wav"
        mix_wavs(d / "mic.wav", d / "sys.wav", out)
        _, data = wavfile.read(str(out))
        assert len(data) == 4  # pads shorter to length of longer
