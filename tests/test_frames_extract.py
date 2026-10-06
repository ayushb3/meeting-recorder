"""Frame extraction for imported video: ffmpeg is mocked, images are synthetic."""
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from pipeline.frames import (
    MAX_FRAMES,
    dedupe_frames,
    effective_interval,
    extract_frames,
    has_video,
)
from pipeline.importer import AudioImportError
from transcriber.whisper import Segment


def _png(path: Path, value: int, size=(64, 64)) -> Path:
    Image.new("L", size, value).save(path)
    return path


def _proc(stdout="", returncode=0, stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestHasVideo:
    def test_video_stream(self, tmp_path):
        with patch("pipeline.frames.subprocess.run", return_value=_proc("video,0\n")):
            assert has_video(tmp_path / "a.mp4") is True

    def test_audio_only_has_no_rows(self, tmp_path):
        with patch("pipeline.frames.subprocess.run", return_value=_proc("")):
            assert has_video(tmp_path / "a.m4a") is False

    def test_cover_art_is_not_video(self, tmp_path):
        with patch("pipeline.frames.subprocess.run", return_value=_proc("video,1\n")):
            assert has_video(tmp_path / "a.mp3") is False

    def test_ffprobe_failure_is_false(self, tmp_path):
        with patch("pipeline.frames.subprocess.run", return_value=_proc(returncode=1)):
            assert has_video(tmp_path / "a.mp4") is False


class TestEffectiveInterval:
    def test_short_recording_keeps_requested_interval(self):
        assert effective_interval(10, 600) == 10

    def test_long_recording_is_capped(self):
        assert effective_interval(1, MAX_FRAMES * 10) == 10

    def test_rejects_zero(self):
        with pytest.raises(ValueError):
            effective_interval(0, 100)


class TestDedupe:
    def test_drops_identical_keeps_distinct(self, tmp_path):
        frames = [
            (0, _png(tmp_path / "a.png", 10)),
            (10, _png(tmp_path / "b.png", 10)),
            (20, _png(tmp_path / "c.png", 200)),
            (30, _png(tmp_path / "d.png", 200)),
        ]
        kept = dedupe_frames(frames)
        assert [s for s, _ in kept] == [0, 20]

    def test_compares_against_last_kept_not_previous(self, tmp_path):
        # Each step is below the threshold, but the drift from the kept frame is not.
        frames = [(i * 10, _png(tmp_path / f"{i}.png", 100 + i)) for i in range(0, 6)]
        kept = dedupe_frames(frames, threshold=2.5)
        assert [s for s, _ in kept] == [0, 30]

    def test_no_pillow_keeps_everything(self, tmp_path):
        frames = [(0, tmp_path / "a.png"), (10, tmp_path / "b.png")]
        with patch.dict("sys.modules", {"PIL": None}):
            assert dedupe_frames(frames) == frames


class TestExtractFrames:
    def _ffmpeg(self, out_dir: Path, values: list[int]):
        def run(cmd, **_):
            if "ffprobe" in cmd[0]:
                return _proc("video,0\n")
            for i, v in enumerate(values):
                _png(out_dir / f"raw-{i:05d}.png", v)
            return _proc()
        return run

    def test_names_by_timestamp_and_dedupes(self, tmp_path):
        out = tmp_path / "frames"
        with patch("pipeline.frames.subprocess.run",
                   side_effect=self._ffmpeg(out, [10, 10, 200, 200, 10])):
            result = extract_frames(
                tmp_path / "in.mp4", out, every_s=30, duration_s=150,
                ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"),
            )
        assert result == [(0, "frame-0000.png"), (60, "frame-0100.png"), (120, "frame-0200.png")]
        assert sorted(p.name for p in out.iterdir()) == [n for _, n in result]

    def test_audio_only_skips_ffmpeg(self, tmp_path):
        with patch("pipeline.frames.subprocess.run", return_value=_proc("")) as run:
            assert extract_frames(tmp_path / "in.m4a", tmp_path / "f", 10) == []
        assert run.call_count == 1  # only the ffprobe check

    def test_ffmpeg_failure_raises(self, tmp_path):
        def run(cmd, **_):
            return _proc("video,0\n") if "ffprobe" in cmd[0] else _proc(returncode=1, stderr="boom")
        with patch("pipeline.frames.subprocess.run", side_effect=run):
            with pytest.raises(AudioImportError):
                extract_frames(tmp_path / "in.mp4", tmp_path / "f", 10,
                               ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"))

    def test_source_is_only_read(self, tmp_path):
        source = tmp_path / "in.mp4"
        source.write_bytes(b"video")
        out = tmp_path / "frames"
        with patch("pipeline.frames.subprocess.run",
                   side_effect=self._ffmpeg(out, [10])):
            extract_frames(source, out, 10, ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"))
        assert source.read_bytes() == b"video"


class TestPipelineEmbedsFrames:
    def _run(self, tmp_path, frames, frames_dir):
        from pipeline.processor import run_pipeline

        source = tmp_path / "in.wav"
        source.write_bytes(b"x")
        segs = [Segment(start_seconds=5.0, text="Hello.", source="system"),
                Segment(start_seconds=25.0, text="Later.", source="system")]
        with patch("pipeline.processor.transcribe_raw", return_value=segs), \
             patch("pipeline.processor.summarize", return_value="## Summary"), \
             patch("pipeline.processor.write_note",
                   return_value=tmp_path / "meeting.md") as write_note:
            result = run_pipeline(
                mic_path=source, system_path=source,
                session_dt=datetime(2026, 3, 15, 10, 0), duration_seconds=60,
                output_dir=tmp_path / "out", whisper_binary=Path("w"),
                whisper_model=Path("m"), ollama_model="m", ollama_host="h",
                keep_audio=False, meeting_name="Demo", single_source=source,
                frames=frames, frames_dir=frames_dir,
            )
        return result, write_note

    def test_frames_land_beside_note_and_interleave(self, tmp_path):
        fdir = tmp_path / "frames"
        fdir.mkdir()
        _png(fdir / "frame-0010.png", 50)
        result, write_note = self._run(tmp_path, [(10, "frame-0010.png")], fdir)

        assert (result.session_dir / "frame-0010.png").exists()
        lines = write_note.call_args.kwargs["transcript_lines"]
        assert lines.index("![[frame-0010.png]]") < next(
            i for i, l in enumerate(lines) if "Later." in l
        )
        assert lines.index("![[frame-0010.png]]") > next(
            i for i, l in enumerate(lines) if "Hello." in l
        )

    def test_no_frames_leaves_transcript_unchanged(self, tmp_path):
        _, with_none = self._run(tmp_path, None, None)
        lines = with_none.call_args.kwargs["transcript_lines"]
        assert not any(l.startswith("![[") for l in lines)
