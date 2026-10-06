from notes.frames import frame_filename, interleave_frames, place_frames


def test_place_frames_copies_existing_and_skips_missing(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "frame-0010.png").write_bytes(b"png")

    landed = place_frames(
        [(10, "frame-0010.png"), (20, "frame-0020.png")], src, dst
    )

    assert landed == [(10, "frame-0010.png")]
    assert (dst / "frame-0010.png").read_bytes() == b"png"
    assert not (dst / "frame-0020.png").exists()


def test_helpers_importable_from_shared_module():
    assert frame_filename(466) == "frame-0746.png"
    assert interleave_frames(["[00:05] hi"], []) == ["[00:05] hi"]
