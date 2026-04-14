import os
import shutil
import zipfile
from setuptools import setup
from setuptools.dist import Distribution


APP = ["app.py"]
DATA_FILES = [
    ("assets", ["assets/icon.png", "assets/icon-recording.png"]),
    ("", ["config.template.toml", "setup-guide.md"]),
]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "assets/icon.icns",  # will fall back gracefully if missing
    "plist": {
        "CFBundleName": "Meeting Recorder",
        "CFBundleDisplayName": "Meeting Recorder",
        "CFBundleIdentifier": "com.ayushb.meeting-recorder",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSUIElement": True,  # hides from Dock, menu bar only
        "NSMicrophoneUsageDescription": "Meeting Recorder needs microphone access to record meetings.",
        "NSAppleEventsUsageDescription": "Meeting Recorder uses Apple Events for notifications.",
    },
    "packages": [
        "rumps", "sounddevice", "_sounddevice_data", "soundfile", "_soundfile_data",
        "scipy", "numpy", "requests", "recorder", "transcriber", "summarizer", "notes",
        "pipeline", "ui",
    ],
    "excludes": ["tkinter", "matplotlib", "PyQt5", "wx"],
}


def _unzip_dylib_data():
    """
    py2app zips all packages into pythonXYZ.zip, but sounddevice and soundfile
    load dylibs via dlopen which cannot open files inside a zip archive.

    After py2app finishes: extract *_data packages containing .dylib files
    from the zip into the lib/ directory so they're accessible on disk.
    """
    import glob as _glob
    apps = _glob.glob("dist/*.app")
    if not apps:
        return
    app_path = apps[0]
    lib_dir = os.path.join(app_path, "Contents", "Resources", "lib")
    zip_files = [f for f in os.listdir(lib_dir) if f.startswith("python") and f.endswith(".zip")]
    for zip_name in zip_files:
        zip_path = os.path.join(lib_dir, zip_name)
        # Find packages that contain .dylib files
        with zipfile.ZipFile(zip_path, "r") as zf:
            all_members = zf.namelist()
            # Find top-level packages that contain .dylib files
            dylib_pkgs = set()
            for m in all_members:
                if m.endswith(".dylib"):
                    pkg = m.split("/")[0]
                    dylib_pkgs.add(pkg)
            if not dylib_pkgs:
                continue
            members_to_extract = [m for m in all_members if m.split("/")[0] in dylib_pkgs]
            # Determine unzip destination (lib/pythonX.Y/ is on sys.path in the bundle)
            py_dirs = [d for d in os.listdir(lib_dir) if d.startswith("python") and os.path.isdir(os.path.join(lib_dir, d))]
            dest_base = os.path.join(lib_dir, py_dirs[0]) if py_dirs else lib_dir
            for member in members_to_extract:
                dest = os.path.join(dest_base, member)
                if member.endswith("/"):
                    os.makedirs(dest, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(member) as src, open(dest, "wb") as dst:
                        dst.write(src.read())
            _remove_from_zip(zip_path, members_to_extract)
        print(f"Extracted dylib packages {dylib_pkgs} from {zip_name}")


def _remove_from_zip(zip_path, members_to_remove):
    """Rebuild a zip without the listed member entries."""
    import tempfile
    members_set = set(members_to_remove)
    tmp = zip_path + ".tmp"
    with zipfile.ZipFile(zip_path, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename not in members_set:
                zout.writestr(item, zin.read(item.filename))
    os.replace(tmp, zip_path)


setup(
    name="Meeting Recorder",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

# Run post-build fix when invoked as py2app
import sys
if "py2app" in sys.argv:
    _unzip_dylib_data()
