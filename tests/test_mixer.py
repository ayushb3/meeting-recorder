import numpy as np
import tempfile
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
        # Padded region: [3000+0]//2=1500, [4000+0]//2=2000
        np.testing.assert_array_equal(data[2:], np.array([1500, 2000], dtype=np.int16))


def test_mix_raises_on_sample_rate_mismatch(tmp_path):
    from recorder.mixer import mix_wavs
    from scipy.io import wavfile as wf
    import pytest
    a = np.array([1000, 2000], dtype=np.int16)
    b = np.array([1000, 2000], dtype=np.int16)
    make_wav(tmp_path / "mic.wav", a, rate=16000)
    wf.write(str(tmp_path / "sys.wav"), 44100, b)
    with pytest.raises(ValueError, match="Sample rate mismatch"):
        mix_wavs(tmp_path / "mic.wav", tmp_path / "sys.wav", tmp_path / "out.wav")
