"""System audio capture via a Core Audio process tap.

A process tap reads the system's outgoing audio from the audio path itself,
before it reaches whatever the user is listening on.  That makes capture
independent of the output device, so no loopback driver (BlackHole) and no
Multi-Output Device are needed, and Bluetooth output — whose sample rate rarely
matches a virtual device's — works like anything else.

Requires macOS 14.2 or newer.  Two things are easy to get wrong and both fail
*silently*, producing a well-formed stream of zeroes rather than an error:

* The bundle must declare ``NSAudioCaptureUsageDescription``.  macOS gates taps
  behind it separately from the microphone key.
* The process must be launched through LaunchServices (Finder, Dock, ``open``).
  A binary started straight from a shell inherits the terminal's privacy
  context and is denied without being told.

Because of that, callers should treat an all-zero capture as a failure worth
reporting rather than as a quiet meeting.
"""
from __future__ import annotations

import ctypes
import logging
import platform
import threading
import uuid

import numpy as np

log = logging.getLogger(__name__)


class SystemTapUnavailable(Exception):
    """Raised when a tap cannot be created on this machine."""


# ---------------------------------------------------------------------------
# Core Audio bindings.  The C entry points come through ctypes;
# CATapDescription is an Objective-C class and comes through PyObjC.
# ---------------------------------------------------------------------------

_BINDINGS_ERROR: str | None = None

try:  # pragma: no cover - import-time platform probing
    import objc
    from Foundation import NSMutableArray, NSMutableDictionary, NSNumber

    _CoreAudio = ctypes.CDLL(
        "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
    )
    objc.loadBundle(
        "CoreAudio", globals(),
        bundle_path="/System/Library/Frameworks/CoreAudio.framework",
    )
    CATapDescription = objc.lookUpClass("CATapDescription")
except Exception as exc:  # pragma: no cover - only on non-macOS / old macOS
    _BINDINGS_ERROR = str(exc)
    _CoreAudio = None
    CATapDescription = None


OSStatus = ctypes.c_int32
AudioObjectID = ctypes.c_uint32
UInt32 = ctypes.c_uint32


def _fourcc(s: str) -> int:
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


kAudioObjectPropertyScopeGlobal = _fourcc("glob")
kAudioTapPropertyFormat = _fourcc("tfmt")

# Aggregate-device dictionary keys.  These are the short forms the CoreAudio
# headers actually use; the long symbolic names are not accepted.
_AGG_NAME = "name"
_AGG_UID = "uid"
_AGG_PRIVATE = "private"
_AGG_STACKED = "stacked"
_AGG_TAP_AUTOSTART = "tapautostart"
_AGG_SUBDEVICES = "subdevices"
_AGG_TAPS = "taps"
_SUBTAP_UID = "uid"
_SUBTAP_DRIFT = "drift"


class _PropertyAddress(ctypes.Structure):
    _fields_ = [("mSelector", UInt32), ("mScope", UInt32), ("mElement", UInt32)]


class _StreamBasicDescription(ctypes.Structure):
    _fields_ = [
        ("mSampleRate", ctypes.c_double),
        ("mFormatID", UInt32),
        ("mFormatFlags", UInt32),
        ("mBytesPerPacket", UInt32),
        ("mFramesPerPacket", UInt32),
        ("mBytesPerFrame", UInt32),
        ("mChannelsPerFrame", UInt32),
        ("mBitsPerChannel", UInt32),
        ("mReserved", UInt32),
    ]


class _Buffer(ctypes.Structure):
    _fields_ = [("mNumberChannels", UInt32), ("mDataByteSize", UInt32),
                ("mData", ctypes.c_void_p)]


class _BufferList(ctypes.Structure):
    _fields_ = [("mNumberBuffers", UInt32), ("mBuffers", _Buffer * 8)]


class _TimeStamp(ctypes.Structure):
    _fields_ = [
        ("mSampleTime", ctypes.c_double),
        ("mHostTime", ctypes.c_uint64),
        ("mRateScalar", ctypes.c_double),
        ("mWordClockTime", ctypes.c_uint64),
        ("mSMPTETime", ctypes.c_uint8 * 16),
        ("mFlags", UInt32),
        ("mReserved", UInt32),
    ]


_IOProc = ctypes.CFUNCTYPE(
    OSStatus,
    AudioObjectID,
    ctypes.POINTER(_TimeStamp),
    ctypes.POINTER(_BufferList),
    ctypes.POINTER(_TimeStamp),
    ctypes.POINTER(_BufferList),
    ctypes.POINTER(_TimeStamp),
    ctypes.c_void_p,
)

if _CoreAudio is not None:  # pragma: no branch
    _CoreAudio.AudioHardwareCreateProcessTap.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(AudioObjectID)]
    _CoreAudio.AudioHardwareCreateProcessTap.restype = OSStatus
    _CoreAudio.AudioHardwareDestroyProcessTap.argtypes = [AudioObjectID]
    _CoreAudio.AudioHardwareDestroyProcessTap.restype = OSStatus
    _CoreAudio.AudioHardwareCreateAggregateDevice.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(AudioObjectID)]
    _CoreAudio.AudioHardwareCreateAggregateDevice.restype = OSStatus
    _CoreAudio.AudioHardwareDestroyAggregateDevice.argtypes = [AudioObjectID]
    _CoreAudio.AudioHardwareDestroyAggregateDevice.restype = OSStatus
    _CoreAudio.AudioObjectGetPropertyData.argtypes = [
        AudioObjectID, ctypes.POINTER(_PropertyAddress), UInt32,
        ctypes.c_void_p, ctypes.POINTER(UInt32), ctypes.c_void_p]
    _CoreAudio.AudioObjectGetPropertyData.restype = OSStatus
    _CoreAudio.AudioDeviceCreateIOProcID.argtypes = [
        AudioObjectID, _IOProc, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    _CoreAudio.AudioDeviceCreateIOProcID.restype = OSStatus
    _CoreAudio.AudioDeviceDestroyIOProcID.argtypes = [
        AudioObjectID, ctypes.c_void_p]
    _CoreAudio.AudioDeviceDestroyIOProcID.restype = OSStatus
    _CoreAudio.AudioDeviceStart.argtypes = [AudioObjectID, ctypes.c_void_p]
    _CoreAudio.AudioDeviceStart.restype = OSStatus
    _CoreAudio.AudioDeviceStop.argtypes = [AudioObjectID, ctypes.c_void_p]
    _CoreAudio.AudioDeviceStop.restype = OSStatus


def _status_str(st: int) -> str:
    """Render an OSStatus, showing the four-character code when it is one."""
    raw = st & 0xFFFFFFFF
    b = raw.to_bytes(4, "big")
    if all(32 <= c < 127 for c in b):
        return f"{ctypes.c_int32(raw).value} ('{b.decode('ascii')}')"
    return str(ctypes.c_int32(raw).value)


def _objc_ptr(obj):
    return ctypes.c_void_p(objc.pyobjc_id(obj))


# ---------------------------------------------------------------------------
# Support probing and format conversion
# ---------------------------------------------------------------------------

def _macos_version() -> tuple[int, int]:
    try:
        parts = platform.mac_ver()[0].split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return (0, 0)


def tap_supported() -> bool:
    """True when this machine can create a process tap.

    Says nothing about consent: a supported machine that has not granted audio
    capture will still produce silence.
    """
    if _CoreAudio is None or CATapDescription is None:
        return False
    major, minor = _macos_version()
    if major == 0:
        return False
    return (major, minor) >= (14, 2)


def unsupported_reason() -> str:
    """Human-readable explanation of why :func:`tap_supported` is False."""
    if _BINDINGS_ERROR is not None:
        return f"Core Audio bindings unavailable: {_BINDINGS_ERROR}"
    major, minor = _macos_version()
    if (major, minor) < (14, 2):
        return f"macOS {major}.{minor} is older than the required 14.2"
    return "unknown"


def to_mono_16k(samples: np.ndarray, src_rate: float, src_channels: int) -> np.ndarray:
    """Convert interleaved float32 tap audio to int16 mono at 16 kHz.

    Whisper wants 16 kHz mono, and the rest of the pipeline stores that, so the
    conversion happens here rather than spreading tap-specific formats
    downstream.  48 kHz is an exact 3:1 decimation; other rates fall back to
    linear interpolation, which is adequate for speech.
    """
    if samples.size == 0:
        return np.zeros(0, dtype=np.int16)

    if src_channels > 1:
        usable = (samples.size // src_channels) * src_channels
        mono = samples[:usable].reshape(-1, src_channels).mean(axis=1)
    else:
        mono = samples

    target = 16000
    if abs(src_rate - target) > 1:
        ratio = src_rate / target
        if float(ratio).is_integer():
            from scipy.signal import resample_poly
            mono = resample_poly(mono, 1, int(ratio))
        else:
            n_out = int(round(mono.size / ratio))
            if n_out <= 0:
                return np.zeros(0, dtype=np.int16)
            mono = np.interp(
                np.linspace(0, mono.size - 1, n_out),
                np.arange(mono.size),
                mono,
            )

    return (np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16)


# ---------------------------------------------------------------------------
# The tap itself
# ---------------------------------------------------------------------------

class SystemAudioTap:
    """Captures system audio, delivering int16 mono 16 kHz blocks.

    ``callback`` is invoked with one ``numpy`` array per block, mirroring how
    :class:`sounddevice.InputStream` is used elsewhere in :mod:`recorder.audio`
    so the two capture paths are interchangeable.
    """

    def __init__(self, callback):
        self._callback = callback
        self._tap_id: int | None = None
        self._agg_id: int | None = None
        self._proc_id = ctypes.c_void_p()
        # The CFUNCTYPE wrapper must outlive the stream; if it is collected
        # while Core Audio still holds the pointer the process crashes.
        self._io_proc = None
        self._src_rate = 48000.0
        self._src_channels = 2
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        if not tap_supported():
            raise SystemTapUnavailable(unsupported_reason())

        desc = CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_(
            NSMutableArray.array()
        )
        desc.setName_("Meeting Recorder")
        desc.setPrivate_(True)
        # Capture without muting, so the user still hears the meeting.
        desc.setMuteBehavior_(0)
        tap_uid = str(desc.UUID().UUIDString())

        tap = AudioObjectID(0)
        st = _CoreAudio.AudioHardwareCreateProcessTap(
            _objc_ptr(desc), ctypes.byref(tap))
        if st != 0:
            raise SystemTapUnavailable(
                f"could not create process tap: OSStatus {_status_str(st)}")
        self._tap_id = tap.value

        try:
            fmt = _StreamBasicDescription()
            n = UInt32(ctypes.sizeof(fmt))
            addr = _PropertyAddress(
                kAudioTapPropertyFormat, kAudioObjectPropertyScopeGlobal, 0)
            if _CoreAudio.AudioObjectGetPropertyData(
                    tap.value, ctypes.byref(addr), 0, None,
                    ctypes.byref(n), ctypes.byref(fmt)) == 0:
                self._src_rate = fmt.mSampleRate or 48000.0
                self._src_channels = fmt.mChannelsPerFrame or 2
            log.info("Tap format: %.0f Hz x%d ch",
                     self._src_rate, self._src_channels)

            # An aggregate device with no sub-devices carries the tap alone,
            # which is what keeps capture independent of any real device's
            # sample rate.
            d = NSMutableDictionary.dictionary()
            d[_AGG_NAME] = "Meeting Recorder Capture"
            d[_AGG_UID] = f"meeting-recorder-{uuid.uuid4()}"
            d[_AGG_PRIVATE] = NSNumber.numberWithBool_(True)
            d[_AGG_STACKED] = NSNumber.numberWithBool_(False)
            d[_AGG_TAP_AUTOSTART] = NSNumber.numberWithBool_(True)
            d[_AGG_SUBDEVICES] = NSMutableArray.array()
            taps = NSMutableArray.array()
            entry = NSMutableDictionary.dictionary()
            entry[_SUBTAP_UID] = tap_uid
            entry[_SUBTAP_DRIFT] = NSNumber.numberWithBool_(False)
            taps.addObject_(entry)
            d[_AGG_TAPS] = taps

            agg = AudioObjectID(0)
            st = _CoreAudio.AudioHardwareCreateAggregateDevice(
                _objc_ptr(d), ctypes.byref(agg))
            if st != 0:
                raise SystemTapUnavailable(
                    f"could not create aggregate device: "
                    f"OSStatus {_status_str(st)}")
            self._agg_id = agg.value

            self._io_proc = _IOProc(self._render)
            st = _CoreAudio.AudioDeviceCreateIOProcID(
                agg.value, self._io_proc, None, ctypes.byref(self._proc_id))
            if st != 0:
                raise SystemTapUnavailable(
                    f"could not create IO proc: OSStatus {_status_str(st)}")

            st = _CoreAudio.AudioDeviceStart(agg.value, self._proc_id)
            if st != 0:
                raise SystemTapUnavailable(
                    f"could not start capture: OSStatus {_status_str(st)}")
            self._started = True
        except Exception:
            self.stop()
            raise

    def _render(self, device, now, in_data, in_time, out_data, out_time, ctx):
        """Core Audio render callback — runs on a real-time thread.

        Copies out of the Core Audio buffers and hands the block straight on.
        Nothing here may block: no resampling, no file I/O, no locks.  The
        caller's callback is expected to do no more than enqueue.
        """
        try:
            buffers = in_data.contents
            for i in range(buffers.mNumberBuffers):
                buf = buffers.mBuffers[i]
                if not buf.mData or not buf.mDataByteSize:
                    continue
                block = np.ctypeslib.as_array(
                    ctypes.cast(buf.mData, ctypes.POINTER(ctypes.c_float)),
                    (buf.mDataByteSize // 4,),
                ).copy()
                self._callback(block, self._src_rate, self._src_channels)
        except Exception:  # never propagate into Core Audio
            pass
        return 0

    def stop(self) -> None:
        """Tear down in reverse order of creation; safe to call twice."""
        with self._lock:
            if self._agg_id is not None and self._proc_id:
                if self._started:
                    _CoreAudio.AudioDeviceStop(self._agg_id, self._proc_id)
                _CoreAudio.AudioDeviceDestroyIOProcID(
                    self._agg_id, self._proc_id)
                self._proc_id = ctypes.c_void_p()
            if self._agg_id is not None:
                _CoreAudio.AudioHardwareDestroyAggregateDevice(self._agg_id)
                self._agg_id = None
            if self._tap_id is not None:
                _CoreAudio.AudioHardwareDestroyProcessTap(self._tap_id)
                self._tap_id = None
            self._started = False
            self._io_proc = None
