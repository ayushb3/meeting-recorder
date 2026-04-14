# pipeline/processor.py
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notes.writer import week_folder, write_note
from recorder.mixer import mix_wavs
from summarizer.ollama import OllamaUnavailableError, summarize
from transcriber.whisper import TranscriptionError, transcribe

log = logging.getLogger(__name__)


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
    if mic_path != dest_mic:
        if not mic_path.exists():
            return write_error("setup", f"Mic audio file not found: {mic_path}")
        shutil.move(mic_path, dest_mic)
    if system_path != dest_sys:
        if not system_path.exists():
            return write_error("setup", f"System audio file not found: {system_path}")
        shutil.move(system_path, dest_sys)

    # Mix
    try:
        log.info("Mixing audio: %s + %s -> %s", dest_mic.name, dest_sys.name, mixed_path.name)
        mix_wavs(dest_mic, dest_sys, mixed_path)
    except Exception as e:
        log.error("Mix failed: %s", e)
        return write_error("mix", str(e))

    # Transcribe
    try:
        log.info("Transcribing with whisper: model=%s", whisper_model)
        transcript_lines = transcribe(mixed_path, whisper_binary, whisper_model)
        log.info("Transcription done: %d lines", len(transcript_lines))
    except TranscriptionError as e:
        log.error("Transcription failed: %s", e)
        return write_error("transcribe", str(e))

    # Summarize (non-fatal if Ollama down)
    try:
        log.info("Summarizing with Ollama: model=%s", ollama_model)
        summary = summarize(transcript_lines, ollama_model, ollama_host)
        log.info("Summary done (%d chars)", len(summary))
    except OllamaUnavailableError as e:
        log.warning("Ollama unavailable: %s — saving note without summary", e)
        summary = "⚠ Summary unavailable — Ollama was not reachable during processing."

    # Write note
    try:
        log.info("Writing note to %s", output_dir)
        note_path = write_note(
            dt=session_dt,
            duration_seconds=duration_seconds,
            summary=summary,
            transcript_lines=transcript_lines,
            output_dir=output_dir,
        )
        log.info("Note written: %s", note_path)
    except Exception as e:
        log.error("Write note failed: %s", e)
        return write_error("write_note", str(e))

    # Clean up mixed audio if keep_audio is False
    if not keep_audio:
        mixed_path.unlink(missing_ok=True)
        dest_mic.unlink(missing_ok=True)
        dest_sys.unlink(missing_ok=True)

    return PipelineResult(success=True, note_path=note_path)
