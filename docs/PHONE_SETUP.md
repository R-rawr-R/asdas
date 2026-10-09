# Phone camera-source setup

The PC viewer cannot access a phone's camera by itself. The phone must run an app that captures
the camera and serves it on the local network. The viewer needs an **MJPEG over HTTP** stream
(`Content-Type: multipart/x-mixed-replace`).

## Recommended: IP Webcam (Android)

IP Webcam (by Pavel Khlebovich, free on Google Play) is the recommended source. It serves MJPEG
over HTTP and has the controls BabyCue needs: start/stop, camera selection, resolution and quality,
optional login, and an on-screen preview while streaming.

> Menu names below describe recent versions of the app and may differ slightly in yours. Always use
> the address the app actually shows.

1. Install **IP Webcam** from Google Play and grant camera permission. Grant microphone permission
   only if you want audio (the viewer ignores audio).
2. Connect the phone to the **same Wi-Fi network** as the PC.
3. Optional settings before starting:
   - **Video preferences → Main camera**: rear camera (default) or front camera.
   - **Video preferences → Video resolution**: 640×480 or 1280×720 is a good balance. Higher
     resolutions use much more Wi-Fi bandwidth with MJPEG.
   - **Video preferences → Quality**: 50–70 %.
   - **Local broadcasting → Login/password**: set them (recommended; see *Security* below) and
     enter the same values in the viewer's Username/Password fields.
   - **Power management**: keep the screen or the Wi-Fi awake so the stream does not stop when the
     phone goes idle.
   - Leave any **cloud/online broadcasting** options turned off. BabyCue only needs local streaming.
4. Scroll to the bottom and tap **Start server**. The phone shows its camera preview and a URL such
   as `http://192.168.1.25:8080`.
5. In the viewer, enter `http://<that-address>/video`, e.g. `http://192.168.1.25:8080/video`.
   Entering just `192.168.1.25` also works, because the viewer then uses port 8080 and `/video`
   (the IP Webcam defaults).
6. To stop streaming, use **Stop** in the app's menu or close the app. The camera preview on the
   phone shows when the camera is active.

Useful IP Webcam endpoints:

| Path        | What it returns                                   | Works in viewer |
|-------------|---------------------------------------------------|-----------------|
| `/video`    | MJPEG video stream                                | Yes             |
| `/shot.jpg` | One JPEG snapshot                                 | No: the viewer says it's a snapshot, not a stream |
| `/`         | The app's web control page                        | No: the viewer says it's a web page |

You can also open `http://<phone-ip>:8080/` in a PC browser to check the phone is reachable and to
change settings while streaming.

## Alternatives

Any app that serves MJPEG over HTTP should work. Enter the exact URL the app shows, including the
path. Examples (not tested with this project):

- **DroidCam (Android/iOS)**: has an MJPEG endpoint, commonly `http://<ip>:4747/video`. It may
  allow only one viewer at a time.
- **iOS apps**: several "IP camera" apps serve MJPEG. Check the app's documentation for the exact
  path and port.

Apps that only offer RTSP, WebRTC, or their own desktop client are **not supported** by this
version. The viewer rejects `rtsp://` URLs with an explanatory message.

## Security

- MJPEG over plain HTTP is **unencrypted**. Anyone on the same network who can reach the phone's
  port can watch the stream unless you set a login. Even with a login, HTTP Basic credentials can
  be read by anyone who can capture the network traffic.
- Use a trusted, password-protected (WPA2/WPA3) home network. Never port-forward the phone's
  camera port on your router.
- The viewer does **not** save the password. It keeps the URL and username in local settings only.
