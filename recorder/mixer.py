from pathlib import Path
import numpy as np
from scipy.io import wavfile


def mix_wavs(mic_path: Path, system_path: Path, output_path: Path) -> None:
    """Average two WAV files into one. Pads the shorter to the length of the longer."""
    rate_a, data_a = wavfile.read(str(mic_path))
    rate_b, data_b = wavfile.read(str(system_path))

    if rate_a != rate_b:
        raise ValueError(
            f"Sample rate mismatch: mic={rate_a} Hz, system={rate_b} Hz"
        )

    # Ensure mono int16
    if data_a.ndim > 1:
        data_a = data_a.mean(axis=1).astype(np.int16)
    if data_b.ndim > 1:
        data_b = data_b.mean(axis=1).astype(np.int16)

    # Pad shorter array
    max_len = max(len(data_a), len(data_b))
    data_a = np.pad(data_a, (0, max_len - len(data_a)))
    data_b = np.pad(data_b, (0, max_len - len(data_b)))

    mixed = ((data_a.astype(np.int32) + data_b.astype(np.int32)) // 2).astype(np.int16)
    wavfile.write(str(output_path), rate_a, mixed)
