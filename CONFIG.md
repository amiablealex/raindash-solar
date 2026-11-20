# config.json reference

Lives on the Pico next to main.py. Edit it in Thonny (or any editor, then upload it).

| Key | Default | What it does |
|-----|---------|--------------|
| `name` | `"raindash"` | Device name sent with the heartbeat - useful if you have more than one. |
| `wifi` | required | List of networks, tried in order (only those in range). Each has `ssid`, `password`, and the forecast location for that network: `place` (shown on screen), `lat`, `lon`. Take one raindash between houses and it shows the right town. 2.4 GHz only. |
| `rotate` | `90` | `90` = the display's ribbon cable on the right, `270` = on the left. |
| `refresh_min` | `30` | Minutes between updates on USB power, and the fallback if there's no power timer. On battery, the TPL5110's own setting decides. |
| `battery.low_pct` | `20` | Below this, the status line turns black: "charge me". |
| `battery.vsys_scale` | `1.0` | Corrects the Pico's VSYS measurement. With USB plugged in, run `import device; device.vsys()` in Thonny while a multimeter reads VSYS (pin 39 to GND). Set this to *multimeter ÷ Pico reading*. |
| `battery.diode_v` | `0.3` | Voltage dropped by the diode between battery and Pico while it's awake. Fine-tune by comparing the heartbeat's `battery_v` with a multimeter on the cell. |
| `heartbeat.url` | `""` (off) | Where to send a status report each update. Leave empty to disable. |
| `heartbeat.method` | `"POST"` | `POST` sends JSON; `GET` sends the same fields as a query string. |
| `heartbeat.every` | `1` | Send on every Nth update only, to save battery. |

## Heartbeat

Fields: `device`, `version`, `place`, `wake` (update counter), `usb`, `battery_v`, `battery_pct`, `vsys` (raw, after `vsys_scale`), `rssi` (Wi-Fi signal, dBm), `forecast_ok`, `fails` (failed updates in a row), `last_awake_ms` (how long the previous wake took - the main power figure).

Plain `http://` URLs are cheaper than `https://`: the encryption handshake adds about a second of Wi-Fi time to every update.

Examples:

- **Home Assistant**: create an automation with a Webhook trigger, then `{"url": "http://192.168.1.50:8123/api/webhook/YOUR-ID", "method": "POST"}`, using your Home Assistant's IP address (the Pico can't look up `.local` names). The fields arrive as `trigger.json.battery_pct` etc.
- **healthchecks.io** (emails you if it goes quiet): `{"url": "https://hc-ping.com/YOUR-UUID", "method": "POST"}`.
- **Your own web server** (e.g. nginx): `{"url": "http://your.server/raindash-hb", "method": "GET"}` - every report appears in the access log.
