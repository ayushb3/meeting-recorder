from setuptools import setup

APP = ["app.py"]
DATA_FILES = [
    ("assets", ["assets/icon.png", "assets/icon-recording.png"]),
    ("", ["config.toml", "setup-guide.md"]),
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
        "rumps", "sounddevice", "soundfile", "scipy", "numpy", "requests",
        "recorder", "transcriber", "summarizer", "notes", "pipeline", "ui",
    ],
    "excludes": ["tkinter", "matplotlib", "PyQt5", "wx"],
}

setup(
    name="Meeting Recorder",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
