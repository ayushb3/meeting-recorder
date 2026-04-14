# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Meeting Recorder macOS menu bar app

import os
from pathlib import Path

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/icon.png', 'assets'),
        ('assets/icon-recording.png', 'assets'),
        ('assets/icon.icns', '.'),
        ('config.template.toml', '.'),
        ('setup-guide.md', '.'),
    ],
    hiddenimports=[
        'rumps',
        'sounddevice',
        'soundfile',
        'scipy',
        'scipy.signal',
        'numpy',
        'requests',
        'tomllib',
        'recorder',
        'recorder.audio',
        'recorder.mixer',
        'transcriber',
        'transcriber.whisper',
        'summarizer',
        'summarizer.ollama',
        'notes',
        'notes.writer',
        'pipeline',
        'pipeline.processor',
        'ui',
        'ui.menu',
        'config',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'tkinter', 'PyQt5', 'wx'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Meeting Recorder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file='entitlements.plist',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Meeting Recorder',
)

app = BUNDLE(
    coll,
    name='Meeting Recorder.app',
    icon='assets/icon.icns',
    bundle_identifier='com.ayushb.meeting-recorder',
    info_plist={
        'CFBundleName': 'Meeting Recorder',
        'CFBundleDisplayName': 'Meeting Recorder',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'LSUIElement': True,
        'NSMicrophoneUsageDescription': 'Meeting Recorder needs microphone access to record meetings.',
        'NSAppleEventsUsageDescription': 'Meeting Recorder uses Apple Events for notifications.',
        'NSHighResolutionCapable': True,
    },
)
