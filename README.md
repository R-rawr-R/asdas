# BabyCue Camera

Streams a smartphone camera to a Windows PC over the local network and shows it in a Python
desktop viewer. This is the camera part of **BabyCue**, a local, privacy-first infant-monitoring
project. The viewer receives video and runs basic image-quality diagnostics. **It does not do
infant detection yet**, and it never shows invented AI results.

```
 Phone (IP Webcam app)                 PC (this project)
 ┌──────────────────┐   MJPEG/HTTP     ┌─────────────────────────────────────────────┐
 │ camera → HTTP    │ ───────────────▶ │ stream/   MjpegStream: HTTP + MJPEG parser   │
 │ server :8080     │   local network  │ capture/  StreamWorker thread: decode, fps,  │
 └──────────────────┘   only           │           reconnect, latest-frame slot       │
                                       │ processing/ ProcessingRunner thread:         │
                                       │           brightness / dark / blur checks    │
                                       │ ui/       PySide6 viewer (30 Hz refresh)     │
                                       └─────────────────────────────────────────────┘
```

## Quick start (Windows)

Requirements: Windows 10/11 and **Python 3.11 or newer** from python.org (tick "Add python.exe to PATH").

```powershell
git clone https://github.com/R-rawr-R/asdas.git babycue-camera
cd babycue-camera
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
babycue-camera
```

You can also run `python -m babycue_camera`. Options:

```
babycue-camera --url http://192.168.1.25:8080/video --connect
babycue-camera --log-level DEBUG
```

macOS and Linux work the same way (`python3 -m venv .venv && source .venv/bin/activate`).

### Phone side

Install **IP Webcam** on the Android phone, connect it to the same Wi-Fi, tap **Start server**, and
enter the URL it shows plus `/video` (e.g. `http://192.168.1.25:8080/video`) in the viewer.
See [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md) for details, alternative apps and security settings.

### Try it without a phone

A local test server streams a synthetic pattern stamped "SYNTHETIC TEST PATTERN". It is a
developer tool and is never started by the viewer.

```powershell
python -m babycue_camera.devtools.test_pattern_server --port 8081
babycue-camera --url http://127.0.0.1:8081/video --connect
```

Add `--brightness 0.15` to try the low-light diagnostics and enhancement.

## Using the viewer

- **Stream** field: a full URL, or a bare IP / `ip:port` (which uses port 8080 and `/video` by default).
  The URL is checked before connecting.
- **Username / Password**: only needed if login is enabled in the phone app. The password is never saved.
- **Connect / Disconnect / Reconnect**.
- **Status, Address, Resolution, Frame rate, Last frame, Frames**: measured from the frames
  actually received.
- **Not-live indication**: if no new frame arrives for about 2 s (longer for low-fps streams),
  the last frame is dimmed and labelled **STALE FEED**. While reconnecting it shows **NOT LIVE —
  reconnecting**. On disconnect the frame is cleared, never left frozen.
- **Camera processing**: brightness (0–255), lighting (OK / too dark / very dark), sharpness and
  blur, and pipeline status. *Infant detection: not implemented* is shown explicitly.
- **Low-light display enhancement**: gamma + CLAHE applied to the **displayed copy only**, with an
  "Enhanced display (not night vision)" badge. Diagnostics always use the original frame.

## Architecture

| Module | Responsibility |
|---|---|
| `babycue_camera/config.py` | Parse and validate stream URLs; credentials kept out of `url` and `repr` |
| `babycue_camera/stream/mjpeg.py` | `MjpegStream`: HTTP connect, auth (Basic, Digest fallback), content-type checks, error mapping, abortable reads; `MjpegParser`: incremental multipart parser (Content-Length, boundary, or JPEG-marker fallback) |
| `babycue_camera/stream/errors.py` | Error types with user-facing messages and a `retryable` flag |
| `babycue_camera/capture/worker.py` | `StreamWorker`: background thread, JPEG decode, latest-frame slot (old frames dropped), fps/resolution stats, automatic reconnect with backoff |
| `babycue_camera/capture/stale.py` | `StaleDetector`: frame-age threshold that adapts to the frame rate |
| `babycue_camera/processing/base.py` | `FrameProcessor` protocol and `ProcessingPipeline` (read-only frames, failures isolated) |
| `babycue_camera/processing/runner.py` | `ProcessingRunner`: runs the pipeline on its own thread at ≤5 Hz |
| `babycue_camera/processing/diagnostics.py` | `ImageQualityProcessor`: brightness, darkness, Laplacian-variance blur |
| `babycue_camera/processing/enhance.py` | `enhance_low_light`: display-only enhancement, returns a new array |
| `babycue_camera/ui/` | PySide6 `MainWindow` and `VideoView` |
| `babycue_camera/devtools/test_pattern_server.py` | Synthetic MJPEG server for tests and demos |

Threads: capture (network + decode), processing (diagnostics), and the Qt UI thread, which only
converts and paints the newest frame every 33 ms. Slow processing or painting never builds up a
backlog. Frames are simply skipped.

### Adding a computer-vision module

Write a class with a `name` and `process(frame, context) -> ProcessorResult` and add it to the
pipeline in `MainWindow.connect_stream`. `frame` is the original BGR image as a read-only view. If
you need to modify it (for example to enhance it before inference), call `frame.copy()` first. That
way processed images never silently replace the original input.

```python
from babycue_camera.processing import ProcessorResult


class InfantVisibility:
    name = "infant_visibility"

    def process(self, frame, context):
        ...  # run a local model here
        return ProcessorResult(self.name, ok=True, summary="...", values={...})
```

### Protocol choice: MJPEG over HTTP (WebRTC evaluated)

MJPEG is a series of independent JPEGs over one HTTP response. It's simple to receive, robust
to packet loss between frames, and widely supported by phone apps. Its costs are high bandwidth
(roughly 3–10 Mbit/s at 640×480 / 15 fps, more at 720p) and no encryption.

WebRTC (H.264/VP8) would reduce bandwidth several-fold, adapt the bitrate, lower latency, and
encrypt by default (DTLS-SRTP). But it needs a phone-side WebRTC sender (a custom app, or a web
page served over HTTPS because browsers only allow camera access in secure contexts), a signalling
exchange, and a heavier PC stack (`aiortc` + FFmpeg bindings). **Recommendation:** stay with MJPEG
for the MVP. Consider WebRTC if real-device testing shows Wi-Fi bandwidth or latency problems,
or if encrypted transport becomes a requirement.

## Connection methods

See [docs/NETWORK.md](docs/NETWORK.md) for full details and troubleshooting.

- **Wi-Fi / local IP**: primary. Works without internet. The same Wi-Fi name does not guarantee
  the devices can reach each other (AP/client isolation, VPNs).
- **Direct IP / manual URL**: validated input, clear errors, Reconnect button, automatic retry
  after drops.
- **Bluetooth**: investigated and **not implemented**. The bandwidth is far too low for usable
  video. It could be used later for discovery or control only.
- **USB**: optional and documented (`adb forward tcp:8080 tcp:8080`), not tested on a device.

## Privacy and security

- Video goes only between the phone and the PC. There are no cloud services, analytics or
  telemetry, and the system/environment proxy settings are ignored for the stream connection.
- Nothing is recorded. Frames are never written to disk. Only the latest frame is held in memory,
  and it is released on disconnect.
- Logs contain connection events and errors, never image data or passwords. The URL stored in
  settings has no password.
- **The stream is unencrypted HTTP.** Other devices on the same network could view it, and HTTP
  Basic credentials are visible to anyone capturing traffic. Use a trusted WPA2/WPA3 network,
  enable the phone app's login, and never expose the phone's port to the internet. The viewer
  warns when the target is not a private-network address. Do not treat the local network as
  automatically trusted.

## Low-light limitations

- Diagnostics are heuristics (mean grey level < 50 = too dark, < 20 = very dark; Laplacian
  variance < 100 at 320 px width = blurry). They were tuned on synthetic images and should be
  calibrated on real nursery footage. Blur is not judged on dark or featureless frames.
- Software enhancement cannot recover detail the sensor never captured, and it amplifies noise.
  Longer exposure on the phone increases motion blur.
- Ordinary phone cameras usually filter out infrared, so a brightened image is **not** night vision.

## Testing

```powershell
pip install -r requirements-dev.txt
pytest
ruff check .
```

The automated tests (`tests/`) run against the local synthetic MJPEG server on `127.0.0.1` and
need no phone and no internet. They cover:

- URL validation
- MJPEG parsing edge cases
- connecting and reporting resolution and fps
- refused, unreachable, 404, snapshot and HTML endpoints
- malformed frames
- authentication
- reconnect after a dropped connection and after the server was temporarily down
- stale-feed detection and recovery
- releasing the connection on stop (also when the source is frozen)
- checking the app connects only to the stream host
- brightness, darkness and blur detection
- enhancement never modifying the original frame
- processor isolation
- the UI: connect, live stats, stale overlay, disconnect, reconnect, failed connection, password not saved, event-loop responsiveness at 720p

Real-phone checks are in [docs/MANUAL_TESTS.md](docs/MANUAL_TESTS.md).

## Status

**Verified (automated, on Linux with the synthetic server):** everything in the test list above.

**Not yet verified:**

- with a real phone or IP Webcam
- on Windows
- USB through `adb forward`
- over a real Wi-Fi network without internet
- latency and long-running stability

**Remaining work / ideas:**

- Run the manual test plan on real devices and tune the diagnostic thresholds.
- Auto-discovery of the phone (mDNS, or BLE carrying the phone's address).
- Optional HTTPS with certificate pinning for phone apps that support it.
- Infant visibility detection and tracking as `FrameProcessor` modules.
- WebRTC/H.264 if bandwidth or latency requires it.
