# Manual test plan (real phone)

Automated tests use a local synthetic MJPEG server. The checks below need a real phone and have
**not yet been performed**. Record results (date, phone model, app version, network) when you run
them.

Setup: Windows PC with the viewer installed, an Android phone running IP Webcam, and both on the
same home Wi-Fi.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| M1 | Live feed | Start server in app, enter `http://<ip>:8080/video`, Connect | Live video within a few seconds, status *Live*, resolution matches app setting, frame rate > 0 |
| M2 | Latency | Wave a hand in front of the camera | Delay is noticeable but short (aim: under 1 s at 640×480) |
| M3 | Invalid URL | Enter `192.168.1` and `rtsp://...` | Validation error, no connection attempt |
| M4 | Wrong path | Enter `http://<ip>:8080/nope` | "Stream endpoint not found (HTTP 404)" |
| M5 | Wrong IP | Enter an unused LAN IP | "did not respond" message within ~4 s, UI stays usable |
| M6 | App stopped | While streaming, stop the server in the app | Overlay *NOT LIVE — reconnecting*, frozen frame is dimmed; restart server → video resumes automatically |
| M7 | Wi-Fi drop | Turn phone Wi-Fi off for 10 s, then on (IP unchanged) | Reconnecting, then live again |
| M8 | Frozen source | Cover the app with another camera app or lock the phone | *STALE FEED* overlay after ~2 s, fps shows 0 |
| M9 | Resolution change | Change resolution in the app web UI and restart its server | Resolution readout updates |
| M10 | Disconnect | Click Disconnect | Video cleared, status *Disconnected*. App's web UI/connection count shows the client gone |
| M11 | No internet | Unplug the router's WAN cable (or use phone hotspot with mobile data off) | Streaming still works |
| M12 | Low light | Dim the room gradually | Lighting changes to *Too dark* / *Very dark*; enhancement checkbox brightens the display and shows the "not night vision" badge |
| M13 | Blur | Move the phone quickly or defocus | Sharpness shows *blurry* in a well-lit scene |
| M14 | Login | Enable login in the app; connect without and then with credentials | Clear "requires username and password"; works with correct ones |
| M15 | Responsiveness | Stream at 1280×720 with enhancement on; drag and resize the window | No freezing |
| M16 | Long run | Stream for 1 hour | No crash; memory use stays flat in Task Manager |
