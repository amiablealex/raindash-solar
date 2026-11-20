# raindash.py - 24-hour rain forecast on a Pico W + Waveshare 3.7" e-paper.
#
# One update: Wi-Fi on -> clock (NTP) -> Open-Meteo -> heartbeat -> Wi-Fi off ->
# draw -> refresh -> panel to sleep. Layout and wording follow raindash-kindle.
#
# main() picks the mode at power-up:
#   on USB power  -> stays on and updates every refresh_min (mains mode, Thonny works)
#   on battery    -> one update, then asks the TPL5110 to cut the power
#
# From Thonny, create the display FIRST so its 34 KB buffer is taken while memory is empty:
#   import epd; d = epd.EPD()
#   import raindash
#   raindash.run_once(d)                    # one update, nothing else
#   raindash.run_once(d, demo_data=True)    # made-up data, no Wi-Fi
#   raindash.flat_screen(d)                 # preview the "please charge me" screen
import gc
import json
import sys
import math
import time
from array import array

import framebuf
import epd as EPD
import device

VERSION = "0.4.3"
BLACK, LIGHT, DARK, WHITE = EPD.BLACK, EPD.LIGHT, EPD.DARK, EPD.WHITE
W, H = EPD.W, EPD.H

# ---- settings (config.json can override "rotate" and "refresh_min") ----------
MODEL = "ukmo_seamless"          # UKV 2 km over the UK, Global 10 km beyond
Y_MAX = 8.0                      # mm/h, anything above is clipped to the top
Y_TICKS = ((0.5, "0.5"), (1, "1"), (2, "2"), (4, "4"), (8, "8"))
LIGHT_MM, HEAVY_MM = 0.5, 4.0    # "light" / "heavy" gridline labels
BANDS = ((0.2, "drizzle"), (0.5, "light rain"), (2.0, "rain"), (4.0, "heavy rain"))
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
FLAT_V, RECOVER_V = 3.45, 3.65   # stop updating below FLAT_V; resume above RECOVER_V
OFFLINE_AFTER = 3                # failed updates in a row before the screen says so
BACKOFF = 4                      # then (on battery) only retry every 4th wake
_t0 = time.ticks_ms()


def log(*a):
    print("[%5d ms]" % time.ticks_diff(time.ticks_ms(), _t0), *a)


def load_config():
    with open("config.json") as f:
        return json.load(f)


# ---- time helpers -------------------------------------------------------------
# Everything below works in "local seconds": UTC epoch seconds + the UTC offset
# Open-Meteo reports (so BST is handled), decoded with gmtime().
def parse_local(s):              # "2026-11-15T19:00" -> local seconds
    return time.mktime((int(s[0:4]), int(s[5:7]), int(s[8:10]),
                        int(s[11:13]), int(s[14:16]), 0, 0, 0))


def hhmm(t):
    tm = time.gmtime(int(t))
    return "%02d:%02d" % (tm[3], tm[4])


def day_hhmm(t):
    return DAYS[time.gmtime(int(t))[6]] + " " + hhmm(t)


# ---- network ------------------------------------------------------------------
def connect(cfg):
    import network
    import rp2
    rp2.country("GB")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    nets = cfg["wifi"]
    if len(nets) > 1:                         # pick the first configured network in range
        seen = [s[0].decode() for s in wlan.scan()]
        nets = [n for n in nets if n["ssid"] in seen] or nets
    for net in nets:
        log("Wi-Fi: joining", net["ssid"])
        wlan.connect(net["ssid"], net["password"])
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < 15000:
            if wlan.isconnected():
                log("Wi-Fi: connected")
                return wlan, net
            if wlan.status() < 0:             # wrong password / not found
                break
            time.sleep_ms(100)
        log("Wi-Fi: failed, status", wlan.status())
        wlan.disconnect()
    wlan.active(False)
    raise OSError("no Wi-Fi")


def set_clock():
    import ntptime
    for _ in range(3):
        try:
            ntptime.settime()                 # RTC = UTC
            return True
        except Exception:
            time.sleep_ms(500)
    return False


def fetch(lat, lon):
    import requests
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&hourly=precipitation&current=temperature_2m,apparent_temperature"
           "&models=%s&timezone=Europe%%2FLondon&forecast_days=2" % (lat, lon, MODEL))
    r = requests.get(url, timeout=20)
    try:
        if r.status_code != 200:
            raise OSError("Open-Meteo HTTP %d" % r.status_code)
        return r.json()
    finally:
        r.close()


def heartbeat(cfg, info):
    """Optional status report to a URL of the user's choosing (config "heartbeat").
    POST sends JSON (Home Assistant webhooks, healthchecks.io, your own server);
    GET sends the same fields as a query string (shows up in any web server's log)."""
    hb = cfg.get("heartbeat") or {}
    if not hb.get("url") or info["wake"] % max(1, hb.get("every", 1)):
        return
    import requests
    try:
        if hb.get("method", "POST").upper() == "GET":
            q = "&".join("%s=%s" % (k, _quote(v)) for k, v in info.items())
            r = requests.get(hb["url"] + ("&" if "?" in hb["url"] else "?") + q, timeout=10)
        else:
            r = requests.post(hb["url"], json=info, timeout=10)
        log("heartbeat: HTTP", r.status_code)
        r.close()
    except Exception as e:                    # never let reporting break the dashboard
        log("heartbeat failed:", repr(e))


def _quote(v):
    v = str(int(v) if isinstance(v, bool) else v)
    return "".join(c if c.isalpha() or c.isdigit() or c in "-._" else "%%%02X" % ord(c) for c in v)


def parse(resp, now):
    """Open-Meteo JSON -> (points, now, end, temps). Times are local seconds."""
    if now is None:                           # no NTP: fall back to Open-Meteo's own clock
        now = parse_local(resp["current"]["time"])
    hourly = resp["hourly"]
    pts = []
    for t, mm in zip(hourly["time"], hourly["precipitation"]):
        # each value is the total for the PRECEDING hour: centre it on that hour
        pts.append((parse_local(t) - 1800, mm or 0.0))
    cur = resp.get("current", {})
    temps = (cur.get("temperature_2m"), cur.get("apparent_temperature"))
    return window(pts, now) + ((temps if None not in temps else None),)


def window(pts, now):
    end = now + 86400
    keep = [p for p in pts if now - 3600 <= p[0] <= end + 3600]
    if len(keep) < 6:
        raise ValueError("not enough forecast data")
    return keep, now, end


def demo(now):
    """A dry spell, a band of heavy rain, then showers - for testing the look."""
    start = now - now % 3600 - 3600
    pts = []
    for i in range(28):
        h = i - 1
        mm = 3.6 * math.exp(-((h - 7) / 2.2) ** 2) + 0.9 * math.exp(-((h - 15) / 1.2) ** 2)
        mm += 0.35 * math.exp(-((h - 20) / 0.9) ** 2)
        pts.append((start + i * 3600 + 1800, mm))
    return window(pts, now) + ((9.4, 6.2),)


# ---- headline (same rules as the Kindle) --------------------------------------
def band(mm):
    """-1 for dry, else the index of the heaviest band reached."""
    b = -1
    for i, (lo, _) in enumerate(BANDS):
        if mm >= lo:
            b = i
    return b


def headline(pts, now, end):
    # one value per hour; [0] is the current hour. Times are hour starts.
    hours = [(t - 1800, band(mm)) for t, mm in pts if now - 1800 <= t <= end]
    name = lambda b: BANDS[b][1][0].upper() + BANDS[b][1][1:]

    if all(b < 0 for _, b in hours):
        return "Dry for the next 24 hours"
    if all(b >= 0 for _, b in hours):
        peak_t, peak = hours[0]
        for t, b in hours:
            if b > peak:
                peak_t, peak = t, b
        return "Drizzly all day" if peak == 0 else "Wet all day, heaviest at " + hhmm(peak_t)

    if hours[0][1] >= 0:                      # raining now
        spell = []
        for t, b in hours:
            if b < 0:
                break
            spell.append((t, b))
        now_b = hours[0][1]
        peak = max(b for _, b in spell)
        if peak >= 2 and peak > now_b:        # this spell gets properly heavier
            when = [t for t, b in spell if b > now_b][0]
            lead = "Spitting now" if now_b == 0 else name(now_b) + " now"
            return lead + ", heavier from " + hhmm(when)
        lead = "Just spitting" if now_b == 0 else name(now_b) + " now"
        return lead + ", dry by " + hhmm(hours[len(spell)][0])

    # dry now: describe the next spell by its peak
    i = [i for i, (_, b) in enumerate(hours) if b >= 0][0]
    first, peak = hours[i][0], 0
    for _, b in hours[i:]:
        if b < 0:
            break
        peak = max(peak, b)
    if peak == 0:
        return "Chance of drizzle from " + hhmm(first)
    return name(peak) + " from " + hhmm(first)


# ---- curve smoothing (monotone cubic: no overshoot below zero) ----------------
def monotone(xs, ys, steps=6):
    """Yields (x, y) points along a smooth curve through the data (a generator,
    so the Pico never holds the whole curve as float objects)."""
    n = len(xs)
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
                  for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9:
                t = 3 / math.sqrt(s)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    for i in range(n - 1):
        h = xs[i + 1] - xs[i]
        for k in range(steps):
            t = k / steps
            t2, t3 = t * t, t * t * t
            yield (xs[i] + t * h,
                   (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i]
                   + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1])
    yield xs[-1], ys[-1]


# ---- text (bitmap fonts made with font_to_py) ---------------------------------
_pal = framebuf.FrameBuffer(bytearray(1), 2, 1, framebuf.GS2_HMSB)


def text_width(font, s):
    return sum(font.get_ch(c)[2] for c in s)


def text(fb, font, s, x, y, c=BLACK, align="l"):
    """Draw s with its top at y. align: l / r (x is the right edge) / c."""
    if align == "r":
        x -= text_width(font, s)
    elif align == "c":
        x -= text_width(font, s) // 2
    bg = WHITE if c != WHITE else BLACK       # glyph background, made transparent
    _pal.pixel(0, 0, bg)
    _pal.pixel(1, 0, c)
    for ch in s:
        glyph, h, w = font.get_ch(ch)
        fb.blit(framebuf.FrameBuffer(bytearray(glyph), w, h, framebuf.MONO_HLSB), x, y, bg, _pal)
        x += w
    return x


def deg(v):
    r = round(v)
    return ("−%d°" % -r) if r < 0 else ("%d°" % r)


# ---- drawing --------------------------------------------------------------------
def font(name=None):
    """Load one font, releasing any other, so only ~1-2 fonts are ever in RAM.
    font() with no name just releases them."""
    for m in [m for m in sys.modules if m.startswith("font_") and m != name]:
        del sys.modules[m]
    gc.collect()
    return __import__(name) if name else None


def render(fb, pts, now, end, temps, status, alert=False):
    """status: the line under the headline. alert: draw it white-on-black (low battery)."""
    fb.fill(WHITE)
    L, R, T, B = 34, W - 8, 76, H - 24        # plot area
    span = end - now
    xpx = lambda t: L + (R - L) * (t - now) / span
    ypx = lambda mm: B - (B - T) * math.sqrt(min(max(mm, 0), Y_MAX) / Y_MAX)
    hours = []                                # (x, hour of day, weekday) for each whole hour
    t = now - now % 3600 + 3600
    while t <= end:
        tm = time.gmtime(t)
        hours.append((int(xpx(t)), tm[3], tm[6]))
        t += 3600

    # small labels (all outside the rain area, or under it like the Kindle)
    f = font("font_r13")
    for mm, label in Y_TICKS:
        y = int(ypx(mm))
        fb.hline(L, y, R - L, LIGHT)
        text(fb, f, label, L - 5, y - 7, DARK, "r")
    for mm, word in ((LIGHT_MM, "light"), (HEAVY_MM, "heavy")):
        text(fb, f, word, R - 2, int(ypx(mm)) - 14, DARK, "r")
    text(fb, f, "mm/h", 3, T - 23, DARK)
    for x, hour, _ in hours:
        if hour % 3 == 0 and x - L > 46:      # leave room for "now"
            text(fb, f, "%02d" % hour, x, B + 8, BLACK, "c")
    sub_w = 0
    if temps:
        sub = "feels like · actual " + deg(temps[0])
        sub_w = text_width(f, sub)
        text(fb, f, sub, R, 53, DARK, "r")

    # rain area: smoothed curve, clipped to the plot window
    # x as seconds-from-now: the Pico's floats are single precision, too coarse for epoch times
    poly = array("h")                         # x0, y0, x1, y1, ... as small ints
    for x, mm in monotone([p[0] - now for p in pts], [p[1] for p in pts]):
        poly.append(int(min(max(L + (R - L) * x / span, L), R)))
        poly.append(int(ypx(mm)))
    n = len(poly)
    outline = poly[:]
    poly.extend(array("h", (poly[n - 2], B, poly[0], B)))   # close along the baseline
    fb.poly(0, 0, poly, DARK, True)
    for i in range(0, n - 2, 2):              # 2 px outline on top
        x0, y0, x1, y1 = outline[i], outline[i + 1], outline[i + 2], outline[i + 3]
        fb.line(x0, y0, x1, y1, BLACK)
        fb.line(x0, y0 - 1, x1, y1 - 1, BLACK)
        fb.line(x0 + 1, y0, x1 + 1, y1, BLACK)
    del poly, outline

    # baseline + hour ticks + a line at midnight
    fb.fill_rect(L, B, R - L + 1, 2, BLACK)
    for x, hour, _ in hours:
        fb.vline(x, B, 7 if hour % 3 == 0 else 4, BLACK)
        if hour == 0:
            fb.vline(x, T, B - T, DARK)

    # header right: big "feels like" temperature
    head_right = R
    if temps:
        f = font("font_b42")
        big = deg(temps[1])
        text(fb, f, big, R, 4, BLACK, "r")
        head_right = R - max(text_width(f, big), sub_w) - 14

    # header left: headline in the largest size that fits, then the status line
    s = headline(pts, now, end)
    for name, y in (("font_b22", 8), ("font_b18", 10), ("font_b15", 12)):
        f = font(name)
        if text_width(f, s) <= head_right - 10:
            break
    text(fb, f, s, 10, y, BLACK)
    f = font("font_r14")
    if text_width(f, status) > head_right - 10:   # keep clear of the temperature
        status = status.replace("updated ", "")
    if alert:
        fb.fill_rect(4, 35, text_width(f, status) + 12, f.height() + 3, BLACK)
        text(fb, f, status, 10, 37, WHITE)
    else:
        text(fb, f, status, 10, 37, DARK)

    # "now" and day names
    f = font("font_b13")
    for x, hour, wday in hours:
        if hour == 0:
            text(fb, f, DAYS[wday], x + 4, T + 2, DARK)
    text(fb, f, "now", L, B + 8, BLACK)
    f = None
    font()                                    # release the last font before refreshing


def flat_screen(display, last=None):
    """Shown once when the battery is flat. E-ink keeps it with no power at all."""
    fb = display.fb
    fb.fill(WHITE)
    cx = W // 2
    fb.rect(cx - 42, 40, 76, 40, BLACK)       # an empty battery
    fb.rect(cx - 41, 41, 74, 38, BLACK)
    fb.fill_rect(cx + 34, 52, 8, 16, BLACK)
    fb.fill_rect(cx - 36, 46, 8, 28, DARK)
    f = font("font_b22")
    text(fb, f, "Please charge me", cx, 104, BLACK, "c")
    f = font("font_r14")
    text(fb, f, "The battery is flat, so the rain forecast has stopped.", cx, 150, BLACK, "c")
    text(fb, f, "Plug a USB-C charger into the charging port. It starts", cx, 172, BLACK, "c")
    text(fb, f, "again by itself once the battery has some charge.", cx, 190, BLACK, "c")
    if last:
        text(fb, f, "Last forecast: " + day_hhmm(last), cx, 228, DARK, "c")
    f = None
    font()
    refresh(display)


def refresh(display):
    display.init()
    display.show()
    display.sleep()
    log("refreshed (panel busy %d ms)" % display.refresh_ms)


def status_line(place, now, pwr, offline=False):
    s = place + "  ·  updated " + hhmm(now)
    if offline:
        s += "  ·  offline"
    if pwr and not pwr["usb"]:
        low = pwr["pct"] < pwr.get("low", 20)
        s += ("  ·  %d%% - charge me" if low else "  ·  %d%%") % pwr["pct"]
        return s, low
    return s, False


def draw(display, pts, now, end, temps, place, pwr, offline=False):
    status, alert = status_line(place, now, pwr, offline)
    render(display.fb, pts, now, end, temps, status, alert)
    log("drawn")
    refresh(display)


def update(display, cfg, st, pwr):
    """Fetch and show a new forecast. On failure keep the last chart, and after a
    few failures in a row redraw it from the cache with "no connection" added."""
    try:
        wlan, net = connect(cfg)
        resp = None
        try:
            have_clock = set_clock()
            log("clock:", "NTP" if have_clock else "no NTP, using Open-Meteo time")
            try:
                resp = fetch(net["lat"], net["lon"])
                log("fetched")
            finally:
                heartbeat(cfg, {
                    "device": cfg.get("name", "raindash"), "version": VERSION,
                    "place": net["place"], "wake": st.get("wake", 0),
                    "usb": pwr["usb"], "battery_v": pwr["v"] and round(pwr["v"], 2),
                    "battery_pct": pwr["pct"], "vsys": round(pwr["vsys"], 3),
                    "rssi": wlan.status("rssi"),
                    "forecast_ok": resp is not None, "fails": st.get("fails", 0),
                    "last_awake_ms": st.get("awake_ms")})
        finally:
            wlan.disconnect()
            wlan.active(False)                # radio off before the slow part
            log("Wi-Fi off")
        utc = time.time()
        now = (utc - utc % 60 + resp["utc_offset_seconds"]) if have_clock else None
        pts, now, end, temps = parse(resp, now)
        del resp
    except Exception as e:
        st["fails"] = st.get("fails", 0) + 1
        log("update failed (%d in a row): %r" % (st["fails"], e))
        c = st.get("cache")
        if st["fails"] == OFFLINE_AFTER and c:
            draw(display, [tuple(p) for p in c["pts"]], c["now"], c["end"], c["temps"],
                 c["place"], pwr, offline=True)
        return
    gc.collect()
    st["fails"] = 0
    st["cache"] = {"pts": pts, "now": now, "end": end, "temps": temps, "place": net["place"]}
    draw(display, pts, now, end, temps, net["place"], pwr)


def run_once(display, demo_data=False, cfg=None):
    """One update with nothing else (no state, no power handling) - for testing."""
    cfg = cfg or load_config()
    display.rotate = cfg.get("rotate", 90)
    gc.collect()
    log("start, free RAM %d bytes" % gc.mem_free())
    pwr = device.power(cfg)
    if demo_data:
        utc = time.time()
        pts, now, end, temps = demo(utc - utc % 60)
        draw(display, pts, now, end, temps, cfg["wifi"][0]["place"], pwr)
    else:
        update(display, cfg, {}, pwr)
    log("done, free RAM %d bytes" % gc.mem_free())


def main(display):
    """Called by main.py at power-up."""
    t0 = time.ticks_ms()
    cfg = load_config()
    display.rotate = cfg.get("rotate", 90)
    every = cfg.get("refresh_min", 30) * 60
    pwr = device.power(cfg)
    log("raindash %s, VSYS %.2f V, VBUS %d, %s" % (VERSION, pwr["vsys"], pwr["vbus"], "USB power" if pwr["usb"]
        else "battery %.2f V (%d%%)" % (pwr["v"], pwr["pct"])))

    if pwr["usb"]:                            # mains mode: stay on, never cut power
        while True:
            start = time.time()
            st = device.load_state()
            st["wake"] = st.get("wake", 0) + 1
            st["flat"] = False
            try:
                update(display, cfg, st, pwr)
            except Exception as e:
                log("error:", repr(e))
            device.save_state(st)
            # wait for the next update a minute at a time; if USB is unplugged,
            # restart so the battery logic (and the power timer) takes over
            while time.time() - start < every:
                time.sleep(60)
                if not device.usb_power():
                    log("USB unplugged: restarting on battery")
                    import machine
                    machine.reset()
            pwr = device.power(cfg)           # (Wi-Fi is off between updates, so this is safe)

    st = device.load_state()
    st["wake"] = st.get("wake", 0) + 1
    pwr["low"] = cfg.get("battery", {}).get("low_pct", 20)
    try:
        low = pwr["v"] < FLAT_V or (st.get("flat") and pwr["v"] < RECOVER_V)
        st["low"] = st.get("low", 0) + 1 if low else 0
        if st["low"] >= 2 or (low and st.get("flat")):   # two low readings in a row, not one dip
            if not st.get("flat"):            # draw it once, then just sleep
                st["flat"] = True
                flat_screen(display, (st.get("cache") or {}).get("now"))
            log("battery flat: not updating")
        elif st.get("fails", 0) >= OFFLINE_AFTER and st["wake"] % BACKOFF:
            log("offline: skipping this wake to save battery")
        else:
            st["flat"] = False
            update(display, cfg, st, pwr)
    except Exception as e:
        log("error:", repr(e))
    st["awake_ms"] = time.ticks_diff(time.ticks_ms(), t0)
    device.save_state(st)
    device.power_off(every)
