# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Meeting Recorder macOS menu bar app

import os
from pathlib import Path

block_cipher = None

# scripts/ and .venv/ are deliberately not bundled, but menu items that shell
# out need to find them. A frozen app cannot derive the checkout from
# __file__, so record where this build came from.
_repo_root = Path(SPECPATH).resolve()
_build_info = _repo_root / '_build_info.py'
_build_info.write_text(f'REPO_ROOT = {str(_repo_root)!r}\n')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/icon.png', 'assets'),
        ('assets/icon-recording.png', 'assets'),
        ('assets/icon.icns', '.'),
        ('config.template.toml', '.'),
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
        'recorder.systemtap',
        'transcriber',
        'transcriber.whisper',
        'summarizer',
        'summarizer.ollama',
        'notes',
        'notes.writer',
        'notes.frames',
        'pipeline',
        'pipeline.processor',
        'ui',
        'ui.menu',
        'ui.settings_window',
        'ui.stop_dialog',
        # Imported lazily inside menu handlers, so PyInstaller cannot see these
        # statically. Omitting one fails only when the menu item is clicked.
        'ui.transcript_url_dialog',
        'ui.import_recording_dialog',
        'ui.edit_menu',
        'pipeline.importer',
        'config',
        '_build_info',
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
        'NSAudioCaptureUsageDescription': 'Meeting Recorder captures system audio to record meetings.',
        'NSAppleEventsUsageDescription': 'Meeting Recorder uses Apple Events for notifications.',
        'NSHighResolutionCapable': True,
    },
)
