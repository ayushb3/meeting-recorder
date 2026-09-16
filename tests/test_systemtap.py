import numpy as np
import pytest
from unittest.mock import MagicMock, patch


# --------------------------------------------------------------------------
# Format conversion — the tap hands us 48 kHz stereo float32; the rest of the
# pipeline stores 16 kHz mono int16.
# --------------------------------------------------------------------------

def test_to_mono_16k_stereo_48k_decimates_exactly():
    from recorder.systemtap import to_mono_16k

    # One second of 48 kHz stereo -> one second of 16 kHz mono.
    stereo = np.tile(np.array([0.5, -0.5], dtype=np.float32), 48000)
    out = to_mono_16k(stereo, 48000.0, 2)

    assert out.size == 16000
    assert out.dtype == np.int16


def test_to_mono_16k_averages_channels():
    from recorder.systemtap import to_mono_16k

    # Left at +1, right at -1 averages to silence; a naive de-interleave that
    # kept only one channel would come back loud.
    stereo = np.tile(np.array([1.0, -1.0], dtype=np.float32), 16000)
    out = to_mono_16k(stereo, 16000.0, 2)

    assert np.abs(out).max() < 10


def test_to_mono_16k_preserves_signal():
    from recorder.systemtap import to_mono_16k

    tone = np.sin(2 * np.pi * 440 * np.arange(48000) / 48000).astype(np.float32)
    out = to_mono_16k(tone, 48000.0, 1)

    assert out.size == 16000
    assert np.abs(out).max() > 30000  # full-scale tone survives


def test_to_mono_16k_non_integer_ratio():
    from recorder.systemtap import to_mono_16k

    # 44.1 kHz is not an integer multiple of 16 kHz — must still convert.
    tone = np.sin(2 * np.pi * 440 * np.arange(44100) / 44100).astype(np.float32)
    out = to_mono_16k(tone, 44100.0, 1)

    assert 15900 <= out.size <= 16100
    assert np.abs(out).max() > 20000


def test_to_mono_16k_empty_input():
    from recorder.systemtap import to_mono_16k

    out = to_mono_16k(np.zeros(0, dtype=np.float32), 48000.0, 2)
    assert out.size == 0
    assert out.dtype == np.int16


def test_to_mono_16k_odd_stereo_length_does_not_raise():
    from recorder.systemtap import to_mono_16k

    # A truncated block must not blow up the render callback.
    odd = np.ones(4801, dtype=np.float32)
    out = to_mono_16k(odd, 48000.0, 2)
    assert out.dtype == np.int16


# --------------------------------------------------------------------------
# Support probing
# --------------------------------------------------------------------------

def test_tap_supported_requires_macos_14_2():
    import recorder.systemtap as st

    with patch.object(st, "_macos_version", return_value=(14, 1)):
        assert st.tap_supported() is False
    with patch.object(st, "_macos_version", return_value=(14, 2)):
        assert st.tap_supported() is True
    with patch.object(st, "_macos_version", return_value=(26, 6)):
        assert st.tap_supported() is True


def test_unsupported_reason_names_the_version():
    import recorder.systemtap as st

    with patch.object(st, "_macos_version", return_value=(13, 0)):
        assert "13.0" in st.unsupported_reason()


# --------------------------------------------------------------------------
# AudioRecorder capture-path selection
# --------------------------------------------------------------------------

def _patched_recorder(tmp_path, **kwargs):
    from recorder.audio import AudioRecorder

    return AudioRecorder(
        mic_device="default",
        system_device="BlackHole 2ch",
        output_dir=tmp_path,
        session_name="2026-09-16-15h30",
        **kwargs,
    )


def test_recorder_prefers_tap(tmp_path):
    mock_stream = MagicMock()
    fake_tap = MagicMock()

    with patch("recorder.audio.sf.SoundFile"), \
         patch("recorder.audio.sd.InputStream", return_value=mock_stream), \
         patch("recorder.systemtap.SystemAudioTap", return_value=fake_tap):
        rec = _patched_recorder(tmp_path)
        rec.start()
        rec.stop()

    assert rec.system_capture_method == "tap"
    assert fake_tap.start.called
    assert fake_tap.stop.called


def test_recorder_falls_back_to_blackhole_when_tap_unavailable(tmp_path):
    from recorder.systemtap import SystemTapUnavailable

    mock_stream = MagicMock()

    with patch("recorder.audio.sf.SoundFile"), \
         patch("recorder.audio.sd.InputStream", return_value=mock_stream), \
         patch("recorder.systemtap.SystemAudioTap",
               side_effect=SystemTapUnavailable("macOS too old")):
        rec = _patched_recorder(tmp_path)
        rec.start()
        rec.stop()

    assert rec.system_capture_method == "blackhole"
    assert "macOS too old" in rec.tap_error


def test_recorder_capture_method_tap_does_not_fall_back(tmp_path):
    """capture_method="tap" is a diagnostic setting: fail rather than mask."""
    from recorder.systemtap import SystemTapUnavailable

    with patch("recorder.audio.sf.SoundFile"), \
         patch("recorder.audio.sd.InputStream", return_value=MagicMock()), \
         patch("recorder.systemtap.SystemAudioTap",
               side_effect=SystemTapUnavailable("no tap here")):
        rec = _patched_recorder(tmp_path, capture_method="tap")
        with pytest.raises(SystemTapUnavailable):
            rec.start()


def test_recorder_capture_method_blackhole_skips_tap(tmp_path):
    mock_stream = MagicMock()
    fake_tap = MagicMock()

    with patch("recorder.audio.sf.SoundFile"), \
         patch("recorder.audio.sd.InputStream", return_value=mock_stream), \
         patch("recorder.systemtap.SystemAudioTap", return_value=fake_tap):
        rec = _patched_recorder(tmp_path, capture_method="blackhole")
        rec.start()
        rec.stop()

    assert rec.system_capture_method == "blackhole"
    assert not fake_tap.start.called


# --------------------------------------------------------------------------
# Silence guard — capture fails by producing zeroes, not by raising.
# --------------------------------------------------------------------------

def test_system_rms_detects_silence(tmp_path):
    import soundfile as sf

    rec = _patched_recorder(tmp_path)
    sf.write(str(rec.system_path), np.zeros(16000, dtype=np.int16), 16000)

    assert rec.system_rms() < 1e-5


def test_system_rms_detects_audio(tmp_path):
    import soundfile as sf

    rec = _patched_recorder(tmp_path)
    tone = (np.sin(2 * np.pi * 440 * np.arange(16000) / 16000) * 20000).astype(np.int16)
    sf.write(str(rec.system_path), tone, 16000)

    assert rec.system_rms() > 0.1


def test_system_rms_missing_file_is_zero_not_an_error(tmp_path):
    rec = _patched_recorder(tmp_path)
    assert rec.system_rms() == 0.0


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def test_capture_method_defaults_to_auto_when_absent(tmp_path):
    """An existing config with no capture_method must keep working."""
    from config import load_config

    cfg_path = tmp_path / "config.toml"
    binary = tmp_path / "whisper-cli"
    model = tmp_path / "model.bin"
    binary.write_text("")
    model.write_text("")
    cfg_path.write_text(f"""
[paths]
output_dir = "{tmp_path}"
[audio]
system_device = "BlackHole 2ch"
mic_device = "MacBook Pro Microphone"
[whisper]
model = "{model}"
binary = "{binary}"
[ollama]
model = "llama3.1:8b"
host = "http://localhost:11434"
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
""")
    assert load_config(cfg_path).capture_method == "auto"


@pytest.mark.parametrize("value", ["auto", "tap", "blackhole"])
def test_capture_method_accepts_valid_values(tmp_path, value):
    from config import load_config

    cfg_path = tmp_path / "config.toml"
    binary = tmp_path / "whisper-cli"
    model = tmp_path / "model.bin"
    binary.write_text("")
    model.write_text("")
    cfg_path.write_text(f"""
[paths]
output_dir = "{tmp_path}"
[audio]
capture_method = "{value}"
system_device = "BlackHole 2ch"
mic_device = "MacBook Pro Microphone"
[whisper]
model = "{model}"
binary = "{binary}"
[ollama]
model = "llama3.1:8b"
host = "http://localhost:11434"
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
""")
    assert load_config(cfg_path).capture_method == value


def test_capture_method_rejects_unknown_value(tmp_path):
    from config import load_config

    cfg_path = tmp_path / "config.toml"
    binary = tmp_path / "whisper-cli"
    model = tmp_path / "model.bin"
    binary.write_text("")
    model.write_text("")
    cfg_path.write_text(f"""
[paths]
output_dir = "{tmp_path}"
[audio]
capture_method = "magic"
system_device = "BlackHole 2ch"
mic_device = "MacBook Pro Microphone"
[whisper]
model = "{model}"
binary = "{binary}"
[ollama]
model = "llama3.1:8b"
host = "http://localhost:11434"
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
""")
    with pytest.raises(ValueError, match="capture_method"):
        load_config(cfg_path)
