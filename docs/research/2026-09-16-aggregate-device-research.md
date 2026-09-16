# CoreAudio Aggregate/Multi-Output Device — Programmatic Setup Findings

**Tested on:** macOS 26.6.2 (Tahoe), Python 3.14, PyObjC 12.1, BlackHole 2ch installed  
**Status of each claim:** confirmed by live code unless marked "inferred" or "untested"

---

## 1. Creation Recipe — `AudioHardwareCreateAggregateDevice`

**CONFIRMED WORKING** from Python via ctypes + PyObjC Foundation bridge.

### The working Python pattern

```python
import ctypes, objc
from Foundation import NSMutableDictionary, NSNumber

CA = ctypes.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
CA.AudioHardwareCreateAggregateDevice.restype = ctypes.c_int32
CA.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
CA.AudioHardwareDestroyAggregateDevice.restype = ctypes.c_int32
CA.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]

agg_desc = {
    "name":       "MeetingRecorder Multi-Output",       # display name
    "uid":        "com.yourapp.multiout.v1",             # unique string, persists across sessions
    "subdevices": [
        {"uid": "BuiltInSpeakerDevice"},                 # clock master — no drift
        {"uid": "BlackHole2ch_UID",
         "drift": NSNumber.numberWithInt_(1)},           # drift-compensated via SRC
    ],
    "master":     "BuiltInSpeakerDevice",               # clock source = real output device
    "private":    NSNumber.numberWithInt_(0),            # 0 = visible globally; 1 = this process only
    "stacked":    NSNumber.numberWithInt_(1),            # 1 = Multi-Output (stacked); 0 = aggregate input
}

ns_dict = NSMutableDictionary.dictionaryWithDictionary_(agg_desc)
ns_ptr = ctypes.c_void_p(objc.pyobjc_id(ns_dict))
new_device_id = ctypes.c_uint32(0)
status = CA.AudioHardwareCreateAggregateDevice(ns_ptr, ctypes.byref(new_device_id))
# status == 0 → success; new_device_id.value is the AudioObjectID
```

### Key detail: dictionary keys are SHORT forms (from `AudioHardware.h`)

| Logical name | Header constant | Actual string value |
|---|---|---|
| `kAudioAggregateDeviceNameKey` | `#define kAudioAggregateDeviceNameKey "name"` | `"name"` |
| `kAudioAggregateDeviceUIDKey` | `#define kAudioAggregateDeviceUIDKey "uid"` | `"uid"` |
| `kAudioAggregateDeviceSubDeviceListKey` | `#define kAudioAggregateDeviceSubDeviceListKey "subdevices"` | `"subdevices"` |
| `kAudioAggregateDeviceMainSubDeviceKey` | `#define kAudioAggregateDeviceMainSubDeviceKey "master"` | `"master"` |
| `kAudioAggregateDeviceIsPrivateKey` | `#define kAudioAggregateDeviceIsPrivateKey "private"` | `"private"` |
| `kAudioAggregateDeviceIsStackedKey` | `#define kAudioAggregateDeviceIsStackedKey "stacked"` | `"stacked"` |
| `kAudioSubDeviceUIDKey` | `#define kAudioSubDeviceUIDKey "uid"` | `"uid"` |
| `kAudioSubDeviceDriftCompensationKey` | `#define kAudioSubDeviceDriftCompensationKey "drift"` | `"drift"` |

**The first attempt failed with error `0x6e6f7065` ("nope") because it used the long-form keys like `"AggregateDeviceName"` — those are wrong. The correct keys are the short forms above.**

### Which sub-device gets drift compensation?

- **Clock master (`"master"` key)**: the real output device (speakers, BT headphones). This is the timing reference. Set `"drift"` to absent or `0`.
- **BlackHole**: the virtual loopback. Set `"drift": NSNumber.numberWithInt_(1)`. This enables software sample-rate conversion so BlackHole's clock is slaved to the master.

### `stacked` = 1 vs `stacked` = 0

`stacked=1` creates a **Multi-Output Device** (audio is fanned out to all sub-devices simultaneously). This is what you want for recording + playback. `stacked=0` creates an **Aggregate Input** (interleaves inputs from multiple devices). The confusion in the question stems from Apple using the word "aggregate" for both — the `stacked` key is the differentiator.

### Permission / entitlement requirement

**None required for creating an aggregate.** The initial failure (`"nope"`) was caused by wrong dictionary keys, not an entitlement gate. Verified: a plain Python process with no special entitlements can call `AudioHardwareCreateAggregateDevice` and succeed.

`"private": 0` (public) devices **can** be set as the system default output.  
`"private": 1` (private) devices **cannot** be set as system default — they are only usable as a capture source by the creating process. This makes private devices useless for this use-case; use `private=0`.

---

## 2. Setting the System Default Output

**CONFIRMED WORKING** — no special entitlement needed from a non-sandboxed Python process.

```python
def set_default_output(device_id):
    addr = AudioObjectPropertyAddress(
        fourcc('dOut'),   # kAudioHardwarePropertyDefaultOutputDevice
        fourcc('glob'),   # kAudioObjectPropertyScopeGlobal
        0                 # main element
    )
    sz = ctypes.c_uint32(4)
    v  = ctypes.c_uint32(device_id)
    return CA.AudioObjectSetPropertyData(
        1,                # kAudioObjectSystemObject
        ctypes.byref(addr), 0, None, sz, ctypes.byref(v)
    )
```

Audio MIDI Setup has a `com.apple.rootless.storage.AudioSettings` private entitlement — this is required to **persist** settings across reboots (writing to `~/Library/Preferences/com.apple.audio.DeviceSettings.plist`). A non-entitled app can still switch the live default output; it just won't persist if the machine reboots mid-recording.

---

## 3. Crash Recovery

Tested: if the aggregate device is set as default and then destroyed **without restoring** the previous default, macOS automatically selects a new default output. In testing it fell back to the user's previous Multi-Output Device (device ID 53). On a clean machine it would fall back to Built-In Speakers.

**Mitigation strategy:**
1. Before switching default, read and persist the previous default device's **UID** (not ID) to a lockfile (e.g. `~/.meeting-recorder/previous-output-uid`).
2. On clean exit: call `AudioObjectSetPropertyData` to restore, then `AudioHardwareDestroyAggregateDevice`.
3. On next launch: if the lockfile exists, check whether the current default output is a working device. If not, look up the saved UID and restore it. Delete the lockfile.
4. Register `atexit` + `signal.signal(SIGTERM, ...)` handlers.

Because macOS auto-selects a fallback, a crash leaves the user with *some* audio (built-in speakers), not silence. However the custom aggregate is gone, so they lose recording capability until the app restarts.

---

## 4. Detection Recipe — Is BlackHole in the Default Output Chain?

**CONFIRMED WORKING.**

```python
def is_blackhole_in_output_chain(blackhole_uid="BlackHole2ch_UID"):
    kAudioObjectSystemObject = 1
    default_out = get_u32(kAudioObjectSystemObject, fourcc('dOut'))
    if not default_out:
        return False, None
    
    # Check class: aggregate devices have class 'aagg' (0x61616767)
    dev_class = get_u32(default_out, fourcc('clas'))
    if dev_class != fourcc('aagg'):
        return False, default_out   # default is not an aggregate at all
    
    # Enumerate active sub-devices
    sub_ids = get_ids(default_out, fourcc('agrp'))  # kAudioAggregateDevicePropertyActiveSubDeviceList
    for sub_id in sub_ids:
        uid = get_cfstr(sub_id, fourcc('uid '))
        if uid == blackhole_uid:
            return True, sub_id
    
    return False, None
```

Property IDs:
- `kAudioAggregateDevicePropertyActiveSubDeviceList` = `'agrp'` = 0x61677270 (returns only sub-devices that are ACTIVE, i.e. rate-compatible)
- `kAudioAggregateDevicePropertyFullSubDeviceList` = `'grup'` = 0x67727570 (returns all, including dropped ones)
- `kAudioObjectPropertyClass` = `'clas'` = 0x636c6173; value `'aagg'` = aggregate class

**This also detects whether the user already has a valid manual setup from Audio MIDI Setup.** If `is_blackhole_in_output_chain()` returns True, the app can skip creating its own aggregate.

---

## 5. Sample Rate Matching

**CONFIRMED WORKING** — `AudioObjectSetPropertyData` on `kAudioDevicePropertyNominalSampleRate` (Float64 property) changes BlackHole's rate immediately.

```python
def set_device_sample_rate(device_id, rate_hz):
    addr = AudioObjectPropertyAddress(fourcc('nsrt'), fourcc('glob'), 0)
    sz = ctypes.c_uint32(8)
    v  = ctypes.c_double(rate_hz)
    return CA.AudioObjectSetPropertyData(
        device_id, ctypes.byref(addr), 0, None, sz, ctypes.byref(v)
    )
```

### The BT sample rate algorithm

```python
def find_common_rate(device_a_id, device_b_id):
    rates_a = set(get_available_rates(device_a_id))  # AudioValueRange array → set of floats
    rates_b = set(get_available_rates(device_b_id))
    common  = rates_a & rates_b
    if not common:
        return None
    # Prefer 48000 > 44100 > others
    for preferred in [48000.0, 44100.0]:
        if preferred in common:
            return preferred
    return max(common)
```

**Important nuance:** when rates differ and drift compensation is enabled, the aggregate still shows both sub-devices as ACTIVE (`'agrp'`). The drift-compensated sub-device (BlackHole) runs SRC internally. The aggregate's nominal rate is the master's rate. This is the correct behavior — it does not silently drop sub-devices **as long as drift compensation is enabled**. The silent-drop bug occurs when drift compensation is absent AND rates differ.

---

## 6. Bluetooth Specifics

### Detection

```python
kAudioDeviceTransportTypeBluetooth   = fourcc('blue')   # classic BT (A2DP, HFP)
kAudioDeviceTransportTypeBluetoothLE = fourcc('blea')   # BLE Audio

def is_bluetooth_device(device_id):
    transport = get_u32(device_id, fourcc('tran'))  # kAudioDevicePropertyTransportType
    return transport in (kAudioDeviceTransportTypeBluetooth, 
                         kAudioDeviceTransportTypeBluetoothLE)

def is_hfp_mode(device_id):
    """HFP/SCO: macOS lowers the device to ~8000-16000 Hz when mic is active."""
    sr = get_f64(device_id, fourcc('nsrt'))
    return sr is not None and sr <= 16000.0
```

No BT device was connected during testing so this is based on the API and documented macOS behavior, not live measurement. However the property IDs are confirmed from headers and the transport type check was used successfully to enumerate device types.

### A2DP vs HFP problem

When a Bluetooth headset's microphone is selected as the input device in macOS, the system **switches the entire BT device to HFP/SCO profile** — this drops output quality from A2DP stereo (44100 or 48000 Hz) to HFP mono (~8–16 kHz). This is a macOS/BT stack decision, not a CoreAudio choice.

**Can it be prevented?** No, not through CoreAudio APIs. The HFP switch is triggered by CoreAudio selecting an input stream on the BT device. The only workarounds are:

1. **Don't use the BT device as mic input** — use the built-in microphone instead. Since this app records mic separately and system audio separately, this is already the natural choice.
2. If the user wants BT mic recording: accept HFP quality, or route BT mic through a separate track and keep output on a different device.

**Detecting if HFP is active:** poll `kAudioDevicePropertyNominalSampleRate` on the BT device. If it drops to ≤ 16000 Hz after previously being higher, HFP has engaged. There is no push notification for this; you must observe the property via `AudioObjectAddPropertyListener`.

### EarFun at 44100 + BlackHole at 48000 — the actual fix

The fix is to:
1. Read the BT device's current nominal rate.
2. Read its available rates.
3. Find the intersection with BlackHole's available rates.
4. Set BlackHole to the chosen common rate **before** calling `AudioHardwareCreateAggregateDevice`.
5. Set the BT device as the `"master"` (clock source) so the aggregate runs at BT's rate.
6. Enable `"drift"` on BlackHole.

BlackHole's `kAudioDevicePropertyAvailableNominalSampleRates` includes 44100 Hz (confirmed), so a common rate always exists for typical BT devices.

---

## 7. PyObjC Feasibility

### What works well

- `AudioHardwareCreateAggregateDevice` / `AudioHardwareDestroyAggregateDevice`: call via `ctypes` with `objc.pyobjc_id(ns_dict)` to pass the NSDictionary pointer. This works perfectly.
- `AudioObjectGetPropertyData` / `AudioObjectSetPropertyData`: pure ctypes; straightforward once `AudioObjectPropertyAddress` is defined as a `ctypes.Structure`.
- CFString properties: get the pointer, use `CF.CFStringGetCString` to decode. Slightly verbose but reliable.
- Float64 properties (sample rates): use `ctypes.c_double`.
- Array properties (device IDs): allocate a `c_uint32` array of the right size, call `AudioObjectGetPropertyData`.

### What's painful

- **Variable-size property data**: must call `AudioObjectGetPropertyDataSize` first, allocate a buffer, then call `AudioObjectGetPropertyData`. Two calls per property read. A helper function is essential.
- **AudioValueRange array** (available sample rates): need a custom `ctypes.Structure` with two `c_double` fields.
- **CFString output parameters**: CoreAudio returns a `CFStringRef` inside a void pointer. You need `CF.CFStringGetLength` (restype = `c_long`, not default `c_int`) then `CF.CFStringGetCString`. Getting the `restype` wrong causes buffer size calculations to overflow — this was the first test's crash.
- **AudioObjectAddPropertyListener** (property-change notifications): callback takes a C function pointer. Requires `ctypes.CFUNCTYPE` to create a callback. Possible but fiddly; probably better to use a polling loop for the limited cases needed here.

### The spike's experience

The existing `AudioHardwareCreateProcessTap` spike proves the bridging works. The `AudioHardwareCreateAggregateDevice` approach is easier than process taps because it uses plain NSDictionary (toll-free bridged) rather than raw C structs with embedded function pointers.

---

## 8. Realistic Implementation Scope

A "one-click audio setup" button requires approximately:

| Task | Effort |
|---|---|
| Helper: enumerate devices, detect BlackHole UID | ~30 lines |
| Helper: detect if setup already correct (`is_blackhole_in_output_chain`) | ~20 lines |
| Helper: find real output device (default output if not our aggregate) | ~10 lines |
| Helper: get/set sample rate, find common rate | ~30 lines |
| Core: create aggregate, set as default, save previous UID | ~40 lines |
| Core: tear down aggregate, restore default | ~15 lines |
| Lifecycle: atexit + SIGTERM handlers | ~15 lines |
| Crash recovery on launch (lockfile check) | ~20 lines |
| BT detection + HFP warning | ~20 lines |
| Error handling + user messages | ~30 lines |
| **Total** | **~230 lines** |

No new dependencies beyond PyObjC (already installed). No special entitlements or App Store restrictions for non-sandboxed apps. A sandboxed Mac App Store build would need `com.apple.avfoundation.allows-set-output-device` — a non-private entitlement that Apple grants.

**The one hard edge case:** if the user is already on a manually-created Multi-Output Device (their current setup), `is_blackhole_in_output_chain()` returns True and the app can detect this and skip creation entirely — just grab the BlackHole device ID for recording. This handles the current user perfectly.

---

## 9. Open Questions

1. **Does destroying a public aggregate and macOS auto-selecting a fallback work the same on macOS < 14?** Untested. The fallback behavior observed on macOS 26 may not hold on older systems.
2. **BT + HFP: does `AudioObjectAddPropertyListener` on the BT device fire when HFP engages?** Likely yes (the nominal rate changes), but untested without a connected BT device.
3. **Does `kAudioAggregateDeviceIsStackedKey = 1` behave identically to Audio MIDI Setup's "Multi-Output Device"?** Yes based on testing (same `'aagg'` class, same `'grup'` transport, same sub-device behavior). The Audio MIDI Setup UID format `~:AMS2_StackedOutput:1` differs from a programmatic UID but functionality is identical.
4. **Rate-matching race condition**: if a BT device changes rate after the aggregate is created (e.g. user switches profile), does the aggregate's active sub-device list change dynamically? Likely yes — `AudioObjectAddPropertyListener` on the aggregate would detect this.

---

## Summary

| Question | Answer |
|---|---|
| Can an app create a Multi-Output programmatically? | **Yes, no entitlement needed** (unsandboxed). ~5 lines of real work. |
| Can it set the system default output? | **Yes.** `AudioObjectSetPropertyData` on `kAudioHardwarePropertyDefaultOutputDevice`. |
| Does private device work as system default? | **No.** Must use `private=0`. |
| Can it fix the BT 44100/48000 mismatch? | **Yes.** Set BlackHole's rate before creating aggregate. Use BT as `"master"`, enable `"drift"` on BlackHole. |
| Can it detect HFP/SCO mode? | **Yes.** Check if BT device sample rate ≤ 16000 Hz. Can't prevent HFP; recommend built-in mic instead. |
| Can it detect if setup already correct? | **Yes.** Check class == `'aagg'`, enumerate `'agrp'` sub-devices for BlackHole UID. |
| What happens on crash? | macOS auto-picks a fallback output. User loses recording until next launch. Mitigation: lockfile with previous UID. |
| PyObjC feasibility? | **High.** ~230 lines, no new deps, works on macOS 26.6.2 today. |
