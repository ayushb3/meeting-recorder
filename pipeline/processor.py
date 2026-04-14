# pipeline/processor.py
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notes.writer import week_folder, write_note
from summarizer.ollama import OllamaUnavailableError, suggest_title, summarize
from transcriber.whisper import TranscriptionError, merge_transcripts, transcribe_raw

log = logging.getLogger(__name__)




@dataclass
class PipelineResult:
    success: bool
    note_path: Path | None = None
    session_dir: Path | None = None
    meeting_name: str | None = None
    error_stage: str | None = None
    error_message: str | None = None


def _slugify(name: str) -> str:
    return re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", name.strip().lower()))[:60]


def run_pipeline(
    mic_path: Path,
    system_path: Path,
    session_dt: datetime,
    duration_seconds: int,
    output_dir: Path,
    whisper_binary: Path,
    whisper_model: Path,
    ollama_model: str,
    ollama_host: str,
    keep_audio: bool,
    meeting_name: str | None = None,
    llm_context: str | None = None,
) -> PipelineResult:
    week_dir = output_dir / week_folder(session_dt)
    timestamp = session_dt.strftime("%Y-%m-%d-%Hh%M")
    # Use a timestamped placeholder until we know the final name
    session_dir = week_dir / timestamp
    session_dir.mkdir(parents=True, exist_ok=True)

    def write_error(stage: str, message: str) -> PipelineResult:
        error_path = session_dir / f"{stage}.error"
        error_path.write_text(f"stage: {stage}\nerror: {message}\n")
        return PipelineResult(success=False, error_stage=stage, error_message=message)

    # Move raw audio into session folder
    dest_mic = session_dir / "audio-mic.wav"
    dest_sys = session_dir / "audio-system.wav"
    if mic_path != dest_mic:
        if not mic_path.exists():
            return write_error("setup", f"Mic audio file not found: {mic_path}")
        shutil.move(mic_path, dest_mic)
    if system_path != dest_sys:
        if not system_path.exists():
            return write_error("setup", f"System audio file not found: {system_path}")
        shutil.move(system_path, dest_sys)

    # Transcribe both sources and merge
    try:
        log.info("Transcribing system audio: %s", dest_sys.name)
        sys_segments = transcribe_raw(dest_sys, whisper_binary, str(whisper_model), source="system")
        log.info("System transcription: %d segments", len(sys_segments))

        log.info("Transcribing mic audio: %s", dest_mic.name)
        mic_segments = transcribe_raw(dest_mic, whisper_binary, str(whisper_model), source="mic")
        log.info("Mic transcription: %d segments", len(mic_segments))

        transcript_lines = merge_transcripts(sys_segments, mic_segments)
        log.info("Merged transcript: %d lines", len(transcript_lines))
    except TranscriptionError as e:
        log.error("Transcription failed: %s", e)
        return write_error("transcribe", str(e))

    # Summarize (non-fatal if Ollama down)
    try:
        log.info("Summarizing with Ollama: model=%s", ollama_model)
        summary = summarize(transcript_lines, ollama_model, ollama_host, context=llm_context)
        log.info("Summary done (%d chars)", len(summary))

        # If no user-supplied name, ask the LLM to suggest one from the summary
        if not meeting_name:
            meeting_name = suggest_title(summary, ollama_model, ollama_host)
            if meeting_name:
                log.info("LLM suggested title: %r", meeting_name)
    except OllamaUnavailableError as e:
        log.warning("Ollama unavailable: %s — saving note without summary", e)
        summary = "⚠ Summary unavailable — Ollama was not reachable during processing."

    # Rename session dir now that we have a final name
    if meeting_name:
        slug = _slugify(meeting_name)
        named_dir = week_dir / slug
        try:
            session_dir.rename(named_dir)
            session_dir = named_dir
            log.info("Session dir renamed to: %s", session_dir.name)
        except Exception as e:
            log.warning("Could not rename session dir: %s", e)
            # Keep the timestamp dir, update dest paths below
    dest_mic = session_dir / "audio-mic.wav"
    dest_sys = session_dir / "audio-system.wav"
    try:
        log.info("Writing note to %s", session_dir)
        note_path = write_note(
            dt=session_dt,
            duration_seconds=duration_seconds,
            summary=summary,
            transcript_lines=transcript_lines,
            audio_files=[dest_mic, dest_sys],
            output_dir=session_dir,
            overwrite=True,
            meeting_name=meeting_name,
        )
        log.info("Note written: %s", note_path)
    except Exception as e:
        log.error("Write note failed: %s", e)
        return write_error("write_note", str(e))

    # Clean up audio if keep_audio is False
    if not keep_audio:
        dest_mic.unlink(missing_ok=True)
        dest_sys.unlink(missing_ok=True)

    return PipelineResult(success=True, note_path=note_path, session_dir=session_dir, meeting_name=meeting_name)
