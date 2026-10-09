# Connection methods and network troubleshooting

## How the connection works

The phone app runs a small HTTP server. The PC viewer makes an **outgoing** HTTP connection to it
and receives an endless MJPEG response. No internet access, cloud service or account is involved.
Video goes only between the two devices on the local network.

"IP connection" is not a separate wireless technology. An IP address is just how the PC addresses
the phone. The traffic actually travels over Wi-Fi, a phone hotspot, or (optionally) a USB link.

## Method 1: Wi-Fi / local IP (primary)

1. Connect the phone and the PC to the same network.
2. Start streaming in the phone app and note the address it shows.
3. Enter the URL in the viewer and click **Connect**.

### Finding the phone's IP address

- The camera app shows it (IP Webcam shows it at the bottom of the preview).
- Android: **Settings → Wi-Fi → (your network) → IP address** (wording varies by manufacturer).
- iPhone: **Settings → Wi-Fi → (i) next to the network → IP Address**.
- Router admin page: the list of connected/DHCP clients.

### Changing IP addresses

Routers hand out addresses dynamically, so the phone's IP can change after a reboot or reconnection.
If a saved URL stops working with "refused" or "did not respond", check the phone's current IP.
For a stable address, create a **DHCP reservation** for the phone in the router settings.

### Same Wi-Fi name is not enough

Two devices can join the same Wi-Fi name and still be unable to reach each other:

- **Client/AP isolation**: common on guest networks, public Wi-Fi, hotels, campuses and some mesh
  systems. Devices can reach the internet but not each other. Use a home network without isolation,
  turn isolation off in the router, or use a phone hotspot (below).
- **Separate 2.4 GHz and 5 GHz networks or guest SSIDs**: they may be bridged or isolated
  depending on the router.
- **VPN on the PC**: a full-tunnel VPN can send traffic for local addresses into the tunnel.
  Disconnect it or enable "allow LAN access" / split tunnelling.
- **Phone on mobile data**: if Wi-Fi drops, Android may silently switch to mobile data.

### Phone hotspot (no router needed)

Turn on the phone's hotspot, connect the PC to it, then start the camera app. The phone's address
on its own hotspot is shown in the camera app. It is often `192.168.43.1` on older Android versions,
but it varies. This works with no internet at all and avoids router isolation.

### Firewalls

The viewer only makes outgoing connections, so the Windows Firewall normally needs **no inbound
rule**. If the PC uses third-party security software that blocks outgoing connections for
Python, allow `python.exe` (or the venv's `pythonw.exe`). The Windows network profile
(Public/Private) does not matter for outgoing connections.

### Diagnosis checklist

| Viewer message | Meaning | What to try |
|---|---|---|
| *Connection refused* | The phone was reached but nothing listens on that port | Start the server in the app; check the port number |
| *did not respond within N s* | Packets get no answer | Wrong IP, different network, AP isolation, VPN, phone asleep |
| *Stream endpoint not found (HTTP 404)* | Wrong path | Use the path the app shows (IP Webcam: `/video`) |
| *returns a single JPEG snapshot* | Snapshot URL used | Use the video URL instead |
| *returns a web page* | Base URL without the video path | Add `/video` (or the app's path) |
| *requires a username and password* / *rejected* | Login enabled in the app | Enter the same username and password in the viewer |
| *Reconnecting…* | An established stream dropped | Wi-Fi signal, phone sleep, app stopped; the viewer retries automatically |
| *STALE FEED* overlay | Connected but frames stopped arriving | Phone app paused or frozen, or the camera was taken by another app |

Quick checks from the PC (Command Prompt or PowerShell):

```
ping 192.168.1.25
curl.exe -I http://192.168.1.25:8080/video
```

Also try opening the stream URL in a PC web browser. If the browser can't show it either, the
problem is the network or the phone, not the viewer.

## Method 2: Direct IP / manual URL (required)

The URL field accepts:

- a full URL: `http://192.168.1.25:8080/video` (any port and path, plus an optional query string)
- a bare IP or `ip:port`: `192.168.1.25` or `192.168.1.25:8080`, which uses IP Webcam's `/video`
  path and, if no port is given, port 8080
- credentials, either in the separate fields or as `http://user:pass@host:port/path`

URLs are validated before connecting. Typical rejections are a missing host, a malformed IP like
`192.168.1`, an invalid port, a URL with spaces, or an unsupported scheme such as `rtsp://`.
Nothing is hard-coded: the last used URL (without password) is remembered for convenience.
**Reconnect** retries the current address. After a working stream drops, the viewer retries
automatically with backoff (0.5 s, 1 s, 2 s, … up to 10 s).

If you enter an address that is not on a private network, the viewer warns you, because the
stream is unencrypted.

## Method 3: Bluetooth (investigated, not implemented)

Bluetooth is **not a practical video transport** for this project:

- MJPEG at 640×480 and 15 fps needs roughly 3–10 Mbit/s. Bluetooth LE data throughput is
  typically well under 1 Mbit/s in practice, and Classic Bluetooth serial links (SPP) are of the
  same order. Even with heavy compression you would get very low resolution or frame rate.
- No common phone camera app streams video to a PC over Bluetooth. It would need a custom phone
  app and a custom PC receiver, plus pairing and permission handling on both sides.
- Bluetooth PAN (tethering over Bluetooth) gives an IP link, so the same MJPEG URL could in
  theory work over it, but only at a very low frame rate. This has not been tested.

Possible future uses: discovery/pairing (e.g. sending the phone's current IP and port to the PC
over BLE) or control messages. **Wi-Fi/IP stays the video transport.** No Bluetooth feature is
implemented in this version.

## Method 4: USB (optional, documented only)

On Android, USB can carry the same HTTP stream without Wi-Fi:

1. Install Android **platform-tools** (`adb`) on the PC and enable **USB debugging** on the phone.
2. Connect the phone by USB and accept the debugging prompt.
3. Run `adb forward tcp:8080 tcp:8080`.
4. Start the server in IP Webcam and connect the viewer to `http://127.0.0.1:8080/video`.

USB tethering (the phone shares its network over USB) also creates an IP link. Use the phone's
address on that interface. Neither USB method has been tested on a physical device for this project.
