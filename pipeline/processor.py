# pipeline/processor.py
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notes.writer import week_folder, write_note
from summarizer.ollama import OllamaUnavailableError, suggest_title, summarize
from transcriber.whisper import (
    TranscriptionError,
    _segments_to_lines,
    merge_transcripts,
    transcribe_raw,
)

log = logging.getLogger(__name__)




@dataclass
class PipelineResult:
    success: bool
    note_path: Path | None = None
    session_dir: Path | None = None
    meeting_name: str | None = None
    error_stage: str | None = None
    error_message: str | None = None
    summary_ok: bool = True
    warning: str | None = None


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
    ollama_prompt: str | None = None,
    single_source: Path | None = None,
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

    if single_source is not None:
        # --- Single-source import path ---
        # Never move the original file; if keep_audio is true, copy it into the
        # session folder; otherwise transcribe it in place and leave the original
        # entirely alone.  Either way, shutil.move is never called on the user's file.
        if not single_source.exists():
            return write_error("setup", f"Source file not found: {single_source}")

        if keep_audio:
            dest_audio = session_dir / ("audio-import" + single_source.suffix)
            shutil.copy2(single_source, dest_audio)
            audio_for_note: list[Path] = [dest_audio]
        else:
            dest_audio = single_source
            audio_for_note = []  # original lives outside session dir — don't embed

        try:
            log.info("Transcribing imported audio: %s", single_source.name)
            segments = transcribe_raw(dest_audio, whisper_binary, str(whisper_model), source="system")
            log.info("Import transcription: %d segments", len(segments))
            transcript_lines = _segments_to_lines(segments)
            log.info("Transcript lines: %d", len(transcript_lines))
        except TranscriptionError as e:
            log.error("Transcription failed: %s", e)
            return write_error("transcribe", str(e))

    else:
        # --- Normal two-track recorded path ---
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
    _ollama_unavailable = False
    _ollama_warning: str | None = None
    try:
        log.info("Summarizing with Ollama: model=%s", ollama_model)
        summary = summarize(
            transcript_lines, ollama_model, ollama_host,
            context=llm_context, custom_template=ollama_prompt,
        )
        log.info("Summary done (%d chars)", len(summary))

        # If no user-supplied name, ask the LLM to suggest one from the summary
        if not meeting_name:
            meeting_name = suggest_title(summary, ollama_model, ollama_host)
            if meeting_name:
                log.info("LLM suggested title: %r", meeting_name)
    except OllamaUnavailableError as e:
        log.warning("Ollama unavailable: %s — saving note without summary", e)
        summary = "⚠ Summary unavailable — Ollama was not reachable during processing."
        _ollama_unavailable = True
        _ollama_warning = f"Ollama unavailable: {e}. Summary was not generated."

    # Rename session dir now that we have a final name (B6: atomic slug reservation).
    # We must never overwrite an existing dir, including empty ones — POSIX rename(2)
    # silently replaces an empty target directory. We atomically claim the name by
    # mkdir(exist_ok=False); the first candidate that succeeds is ours, then we move
    # the contents of session_dir into it and remove the now-empty source dir.
    if meeting_name:
        slug = _slugify(meeting_name)
        time_hm = session_dt.strftime("-%Hh%M")
        time_hms = session_dt.strftime("-%Hh%Mm%S")
        candidates = [slug, slug + time_hm, slug + time_hms] + [
            f"{slug}{time_hms}-{n}" for n in range(2, 10)
        ]
        reserved: Path | None = None
        for candidate in candidates:
            try:
                candidate_path = week_dir / candidate
                candidate_path.mkdir(parents=False, exist_ok=False)
                reserved = candidate_path
                log.info("Reserved session dir: %s", reserved.name)
                break
            except FileExistsError:
                log.info("Slug candidate taken: %s — trying next", candidate)
        if reserved is not None:
            try:
                # Move contents of session_dir into the reserved dir, then remove source
                for item in session_dir.iterdir():
                    shutil.move(str(item), reserved)
                session_dir.rmdir()
                session_dir = reserved
                log.info("Session dir moved to: %s", session_dir.name)
            except Exception as e:
                log.warning("Could not move session dir contents: %s", e)
                # Leave session_dir as-is; remove the empty reservation to avoid orphans
                try:
                    reserved.rmdir()
                except Exception:
                    pass

    # Write a summarize.error marker when Ollama was unavailable (B2).
    # Must happen AFTER the rename so the marker lands in the final dir.
    # Wrapped so a filesystem error (ENOSPC, read-only) never kills the note write.
    if _ollama_unavailable:
        marker = session_dir / "summarize.error"
        try:
            marker.write_text(f"stage: summarize\nerror: {_ollama_warning}\n")
            log.info("Degraded marker written: %s", marker)
        except Exception as e:
            log.warning("Could not write degraded marker %s: %s", marker, e)
    if single_source is not None:
        # audio_for_note was set in the single-source branch above.
        # Update dest reference in case session_dir was renamed.
        if keep_audio and audio_for_note:
            audio_for_note = [session_dir / audio_for_note[0].name]
        note_audio_files = audio_for_note
    else:
        dest_mic = session_dir / "audio-mic.wav"
        dest_sys = session_dir / "audio-system.wav"
        note_audio_files = [dest_mic, dest_sys]

    try:
        log.info("Writing note to %s", session_dir)
        note_path = write_note(
            dt=session_dt,
            duration_seconds=duration_seconds,
            summary=summary,
            transcript_lines=transcript_lines,
            audio_files=note_audio_files,
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
        if single_source is None:
            dest_mic = session_dir / "audio-mic.wav"
            dest_sys = session_dir / "audio-system.wav"
            dest_mic.unlink(missing_ok=True)
            dest_sys.unlink(missing_ok=True)
        # For single_source: the original file is never touched

    return PipelineResult(
        success=True,
        note_path=note_path,
        session_dir=session_dir,
        meeting_name=meeting_name,
        summary_ok=not _ollama_unavailable,
        warning=_ollama_warning,
    )
