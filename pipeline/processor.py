# pipeline/processor.py
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notes.writer import note_filename, week_folder, write_note
from recorder.mixer import mix_wavs
from summarizer.ollama import OllamaUnavailableError, summarize
from transcriber.whisper import TranscriptionError, transcribe


@dataclass
class PipelineResult:
    success: bool
    note_path: Path | None = None
    error_stage: str | None = None
    error_message: str | None = None


def run_pipeline(
    mic_path: Path,
    system_path: Path,
    session_dt: datetime,
    duration_seconds: int,
    output_dir: Path,
    whisper_binary: Path,
    whisper_model: str,
    ollama_model: str,
    ollama_host: str,
    keep_audio: bool,
) -> PipelineResult:
    week_dir = output_dir / week_folder(session_dt)
    week_dir.mkdir(parents=True, exist_ok=True)
    session_name = session_dt.strftime("%Y-%m-%d-%Hh%M")
    mixed_path = week_dir / f"{session_name}-audio-mixed.wav"

    def write_error(stage: str, message: str) -> PipelineResult:
        error_path = week_dir / f"{session_name}-{stage}.error"
        error_path.write_text(f"stage: {stage}\nerror: {message}\n")
        return PipelineResult(success=False, error_stage=stage, error_message=message)

    # Move raw audio to week folder
    dest_mic = week_dir / mic_path.name
    dest_sys = week_dir / system_path.name
    if mic_path != dest_mic and mic_path.exists():
        shutil.move(str(mic_path), dest_mic)
    if system_path != dest_sys and system_path.exists():
        shutil.move(str(system_path), dest_sys)

    # Mix
    try:
        mix_wavs(dest_mic, dest_sys, mixed_path)
    except Exception as e:
        return write_error("mix", str(e))

    # Transcribe
    try:
        transcript_lines = transcribe(mixed_path, whisper_binary, whisper_model)
    except TranscriptionError as e:
        return write_error("transcribe", str(e))

    # Summarize (non-fatal if Ollama down)
    try:
        summary = summarize(transcript_lines, ollama_model, ollama_host)
    except OllamaUnavailableError:
        summary = "⚠ Summary unavailable — Ollama was not reachable during processing."

    # Write note
    try:
        note_path = write_note(
            dt=session_dt,
            duration_seconds=duration_seconds,
            summary=summary,
            transcript_lines=transcript_lines,
            output_dir=output_dir,
        )
    except Exception as e:
        return write_error("write_note", str(e))

    # Clean up mixed audio if keep_audio is False
    if not keep_audio:
        mixed_path.unlink(missing_ok=True)
        dest_mic.unlink(missing_ok=True)
        dest_sys.unlink(missing_ok=True)

    return PipelineResult(success=True, note_path=note_path)
