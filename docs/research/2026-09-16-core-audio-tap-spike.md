# Core Audio Process Tap from Python — Spike Findings

**Date:** 2026-09-16 | **macOS 26.6.2** (25G83), Apple M4 Pro arm64
**Python 3.14 (Homebrew), PyObjC 12.1** | Repo untouched; all artifacts in this scratchpad.

## VERDICT: FEASIBLE from pure Python — with one hard requirement

`AudioHardwareCreateProcessTap` works from Python/PyObjC, captures non-zero audio,
and is **completely independent of the output device** (no BlackHole needed).
It requires running inside a **bundled `.app` launched via LaunchServices**
(`open` / Dock / Finder) — NOT `python script.py` from a terminal.

## Decisive evidence

Source: synthesized WAV of **440 Hz + 660 Hz** sine tones, played via `afplay`.

### Pure Python, BlackHole removed from output path
`PROOF_python_no_blackhole.wav`, default output = `BuiltInSpeakerDevice`:
```
383488 frames, 2ch, 48000 Hz
RMS = 0.353048   peak = 0.665802   nonzero = 99.7%
dominant frequencies = [440, 660]   <-- exactly the source tones
```
FFT recovering precisely 440/660 Hz proves this is real system audio.

### Output-routing independence (decisive test)
| default output device | tap RMS |
|---|---|
| `Multi-Output Device` (contains BlackHole) | 0.35296 |
| `BuiltInSpeakerDevice` (**no BlackHole**) | 0.35250 |
| `BlackHole2ch_UID` (silent) | 0.35281 |

Statistically identical. The tap captures BEFORE the output device.
Default output was restored to `Multi-Output Device` after every test, verified by read-back.

### Negative control
No audio playing -> `RMS = 0.00000000`. True silence reported for true silence.

### Per-process filtering — works precisely
Two simultaneous `afplay` processes (440 Hz and 1000 Hz), tap targeting one PID:

| tapped process | captured | RMS |
|---|---|---|
| 440 Hz proc | `[440]`, no trace of 1000 | 0.2828 |
| 1000 Hz proc | `[1000]`, no trace of 440 | 0.2824 |

"Capture Teams/Zoom, exclude Spotify" is reachable. `CATapDescription.bundleIDs`
allows filtering by bundle ID — more robust than PID for production.

## THE GOTCHA: why it returns all zeros

Documented failure mode (developer.apple.com/forums/thread/825780). Reproduced extensively.
**Every call returns OSStatus 0. IOProc fires at correct rate (~468 blocks/5s). All samples zero. No error anywhere.**

Ruled out by direct experiment:
- Aggregate config: 9+ combos (sub-device present/absent, tapautostart, drift,
  private/public, stacked, mono/stereo, deviceUID variants). All zero.
- Tap initialisers: global-exclude-none, per-process mixdown, device-scoped. All zero.
- Output routing / BlackHole / Multi-Output. Not the cause.
- PyObjC bugs: wrote a **native ObjC** control (`native_tap.m`) of the identical flow.
  **It returned zeros too** — so this was never a Python problem.

### Actual cause: TCC attribution via LaunchServices
Same signed bundle, two launch methods:
```
./NativeTap.app/Contents/MacOS/nativetap 5   -> RMS=0.00000000  ALL-ZERO
open -W ./NativeTap.app --args 5             -> RMS=0.35295513  SIGNAL
```
Identical binary/code/signature. Only the launch mechanism differs.
Shell-launched inherits the terminal's TCC context with no identity of its own ->
silently denied -> zeros. Via LaunchServices the app is its own responsible process -> audio flows.

**Bundling and signing alone are NOT sufficient — the launch mechanism is the gate.**
Production menu-bar apps launch this way normally, so it is a non-issue in production,
but makes terminal-based development misleading.

### Related: process introspection is separately gated
`kAudioProcessPropertyIsRunningOutput` returns `OSStatus 2003332927 ('who?')`
(`kAudioHardwareBadObjectError`) without the grant. In the Python-in-bundle run,
`introspection_errors=41` **while audio captured fine**. Tap audio and process
metadata are gated independently; `'who?'` does not imply capture will fail.

### PyObjC gotcha: Python hijacks bundle identity
Homebrew Python forces `NSBundle.mainBundle()` to its own internal `Python.app`:
```
bundleID: org.python.python
path: .../Python.framework/Versions/3.14/Resources/Python.app
```
Neither embedding the python binary nor `PYTHONEXECUTABLE` overrode this.
**Audio capture worked anyway** (RMS=0.3535) — the LaunchServices-assigned
responsible process is what matters, not `mainBundle()`. But you cannot rely on
`mainBundle()` inside Python. PyInstaller's bootloader may behave better; verify.

## The recipe that works

### Tap description (ObjC class, bridges normally)
```python
import objc
objc.loadBundle("CoreAudio", globals(),
                bundle_path="/System/Library/Frameworks/CoreAudio.framework")
CATapDescription = objc.lookUpClass("CATapDescription")

desc = CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_([])  # system-wide
# or: CATapDescription.alloc().initStereoMixdownOfProcesses_(ids)            # per-process

desc.setName_("MyTap")
desc.setPrivate_(True)
desc.setMuteBehavior_(0)   # 0=CATapUnmuted (user still hears), 1=Muted, 2=MutedWhenTapped
tap_uid = str(desc.UUID().UUIDString())
```

### C functions — ctypes is simpler than loadBundleFunctions
```python
CoreAudio = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
CoreAudio.AudioHardwareCreateProcessTap.argtypes = [ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_uint32)]
```
Pass ObjC objects as raw pointers: `ctypes.c_void_p(objc.pyobjc_id(desc))`.
Works for both `CATapDescription` and the `NSDictionary` aggregate config.

### Aggregate device — empty sub-device list works
```python
{
  "name": "MyAggregate", "uid": str(uuid.uuid4()),
  "private": True, "stacked": False, "tapautostart": True,
  "subdevices": [],                        # empty is OK -> no sample-rate matching!
  "taps": [{"uid": tap_uid, "drift": False}],
}
```
Read audio from the aggregate's **input** side via a normal `AudioDeviceIOProc`.

### Tap format
`48000 Hz, 2ch, float32 (lpcm, flags 0x9 = float|packed), 8 bytes/frame`.
Read `kAudioTapPropertyFormat` rather than assuming; convert to int16 for WAV.

### Bundling requirement
- Real `Info.plist`: `CFBundleIdentifier`, `CFBundleExecutable`,
  `NSMicrophoneUsageDescription`, `NSAudioCaptureUsageDescription`.
- **Ad-hoc signing sufficed** (`codesign --force -s - --identifier ...`).
  No Developer ID or paid account needed for local use.
- Entitlement used: `com.apple.security.device.audio-input`. App was NOT sandboxed,
  so this may not be strictly required — untested in isolation.
- **Must be launched via LaunchServices.** This is the real requirement.

### Teardown
Destroy IOProc -> aggregate device -> tap, in that order. ~0.4 s settle between
cycles when looping. With correct teardown, **no** need to recreate devices between
runs and no degradation across dozens of cycles — contrary to some forum reports.

## Permission prompts
**No prompt appeared at any point.** Capture began working purely as a function of
LaunchServices launch. The terminal already held microphone consent (verified:
`sounddevice` mic RMS=0.0026, non-zero), which bundles may have inherited.
**Caveat:** on a fresh machine with no prior grant a prompt may appear. Untested.
Plan for a first-run consent dialog and handle denial gracefully.

## ScreenCaptureKit fallback — assessed, not needed
Built `sck.swift` (`SCStream`, `capturesAudio=true`, audio-only). It FAILED:
```
CGPreflightScreenCaptureAccess() = false
SCStreamErrorDomain Code=-3801 "The user declined TCCs for application, window, display capture"
```
- Gated behind **Screen Recording** TCC even for audio-only with 2x2 px video.
  As of macOS 26 there is still no audio-only consent path avoiding that implication.
- Scarier prompt ("wants to record your screen") and worse privacy story.
- CLI binaries cannot trigger the prompt; needs a bundled app + manual System Settings toggle.
- Reachable from PyObjC in principle (`pyobjc-framework-ScreenCaptureKit`, not installed)
  but needs async/delegate plumbing and `CMSampleBuffer` unpacking — more work than the tap.

**Process taps are strictly better:** better permission story, less code,
per-process filtering, no screen-recording prompt.

## PyObjC bridging effort
Low — ~200 lines in `ca.py`, mostly boilerplate property getters.
- `CATapDescription` bridges cleanly as a normal ObjC class.
- C API easier via ctypes; only trick is `objc.pyobjc_id()` for `c_void_p` params.
- IOProc works as `ctypes.CFUNCTYPE`. **Keep a reference to the callback alive**
  or it is garbage collected mid-stream.
- Authoritative headers: `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreAudio.framework/Headers/`
  (`AudioHardwareTapping.h`, `CATapDescription.h`).
- **Real-time safety:** numpy inside the IOProc is fine for a spike; production should
  copy into a lock-free ring buffer and process off-thread. Python's GIL in a CoreAudio
  real-time callback is a genuine dropout risk under load — **main production concern, not de-risked.**

## Production implications
1. **BlackHole can be removed.** Onboarding becomes: install, grant permission, record.
   No kernel extension, no Audio MIDI Setup, no sample-rate matching, no admin rights.
2. **Bluetooth earbuds stop mattering.** Tap is provably output-independent, so the
   EarFun 44100/16000 vs BlackHole 48000 mismatch disappears by construction.
   *(EarFun not connected during spike — mechanism is covered by the routing-independence
   evidence, but an actual BT run is worth doing before shipping.)*
3. **Requires macOS 14.2+.** Older versions need the BlackHole path or a stated minimum OS.
4. **Verify the PyInstaller bundle launches via LaunchServices** and that signing survives
   the build. If its bootloader re-execs and loses responsible-process attribution, this
   breaks — test early.
5. **Always verify RMS at runtime.** Given the silent-zero failure mode, detect an all-zero
   capture and warn rather than save a silent recording.
6. Per-process capture via `bundleIDs` is a free feature worth exposing.

## Open questions
- Fresh machine with no TCC grant — does a prompt appear, and what does it say?
- Does PyInstaller's bundle preserve the needed attribution?
- Real-time robustness of a Python IOProc under CPU load (dropouts).
- Actual Bluetooth earbud capture (device not connected).
- Is the `audio-input` entitlement required at all when unsandboxed?

## Files
| file | purpose |
|---|---|
| `ca.py` | Core Audio ctypes/PyObjC bindings — the reusable piece |
| `PROOF_python_no_blackhole.wav` | **Primary evidence.** Pure Python, no BlackHole, RMS 0.353, 440+660 Hz |
| `PROOF_python_tap.wav` | Python capture with Multi-Output routing |
| `PyTap.app/` | Python tap in a signed bundle (working production-shaped path) |
| `PFTap.app/` + `procfilter.py` | Per-process filtering proof |
| `NativeTap.app/` + `native_tap.m` | ObjC control that isolated the LaunchServices cause |
| `matrix.m`, `devtap.m`, `variants.py` | Configuration sweeps (all-zero, pre-diagnosis) |
| `sck.swift` | ScreenCaptureKit fallback — blocked by screen-recording TCC |
| `setout.m` | Default-output switcher used for routing tests |
| `record.py`, `probe.py` | Original Python spike scripts |
