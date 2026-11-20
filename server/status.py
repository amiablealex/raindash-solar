#!/usr/bin/env python3
"""raindash status page - one static HTML page summarising every device's heartbeats.

Reads the nginx heartbeat logs (Kindle and Pico formats, including rotated .1/.gz
files), works out each device's health, and writes index.html. Run from cron every
few minutes; nginx serves the result to Tailscale addresses only.

    python3 status.py [output.html]
"""
import glob
import gzip
import html
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

OUT = sys.argv[1] if len(sys.argv) > 1 else "/var/www/raindash-status/index.html"
TZ = ZoneInfo("Europe/London")
DAYS = 3                                     # how much history to chart

KINDLE_LOG = os.environ.get("KINDLE_LOG", "/var/log/raindash/heartbeat.log")   # "TIME IP bat=95 chg=1 v=4 200"
PICO_LOG = os.environ.get("PICO_LOG", "/var/log/nginx/raindash-hb.log")        # "TIME IP device=...&battery_v=..."

# Expected minutes between heartbeats, and a friendly name. Unknown devices
# still appear, assuming 30 minutes.
DEVICES = {
    "kindle": {"label": "Kindle", "every": 10},
    "raindash-kitchen": {"label": "Pico solar", "every": 30},
}


# ---- reading the logs ----------------------------------------------------------
def lines(path):
    """Lines from a log and its rotated copies, oldest file first."""
    rotated = []
    for p in glob.glob(path + ".*"):
        n = p[len(path) + 1:].replace(".gz", "")
        if n.isdigit():
            rotated.append((int(n), p))
    for _, p in sorted(rotated, reverse=True) + [(0, path)]:
        try:
            with (gzip.open(p, "rt") if p.endswith(".gz") else open(p)) as f:
                yield from f
        except (OSError, EOFError):
            continue


def parse_time(s):
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None                           # e.g. an old line in nginx's default format


def read_kindle(path):
    for line in lines(path):
        parts = line.split()
        t = parse_time(parts[0]) if parts else None
        if not t:
            continue
        kv = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
        yield "kindle", {
            "t": t, "ip": parts[1], "pct": num(kv.get("bat")),
            "charging": kv.get("chg") == "1", "version": kv.get("v"),
            "ok": parts[-1] in ("200", "304"),
        }


def read_pico(path):
    for line in lines(path):
        parts = line.split(" ", 2)
        t = parse_time(parts[0]) if len(parts) == 3 else None
        if not t:
            continue
        q = {k: v[0] for k, v in parse_qs(parts[2].strip()).items()}
        dev = q.get("device")
        if not dev or dev == "test":
            continue
        yield dev, {
            "t": t, "ip": parts[1], "pct": num(q.get("battery_pct")),
            "v": num(q.get("battery_v")), "usb": q.get("usb") == "1",
            "rssi": num(q.get("rssi")), "awake": num(q.get("last_awake_ms")),
            "fails": int(num(q.get("fails")) or 0), "ok": q.get("forecast_ok") == "1",
            "place": q.get("place"), "version": q.get("version"),
        }


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ---- health ----------------------------------------------------------------------
GOOD, WARN, SERIOUS, CRIT = "good", "warning", "serious", "critical"
ICON = {GOOD: "●", WARN: "▲", SERIOUS: "▲", CRIT: "■"}
RANK = [GOOD, WARN, SERIOUS, CRIT]


def health(beats, every, now):
    last = beats[-1]
    age = (now - last["t"]).total_seconds() / 60
    state, notes = GOOD, []

    def worse(s, note):
        nonlocal state
        notes.append(note)
        if RANK.index(s) > RANK.index(state):
            state = s

    if age > every * 4:
        worse(CRIT, "silent for %s" % ago(age))
    elif age > every * 1.5 + 2:
        worse(WARN, "late")
    if last.get("fails"):
        worse(WARN, "%d failed updates in a row" % last["fails"])
    elif not last["ok"]:
        worse(WARN, "last update failed")
    if last.get("pct") is not None and not last.get("usb") and not last.get("charging"):
        if last["pct"] < 10:
            worse(CRIT, "battery nearly flat")
        elif last["pct"] < 20:
            worse(SERIOUS, "battery low")
    day = [b for b in beats if b["t"] > now - timedelta(hours=24)]
    expected = 24 * 60 / every
    return state, notes, age, min(100, round(100 * len(day) / expected))


def ago(minutes):
    if minutes < 90:
        return "%d min" % minutes
    if minutes < 48 * 60:
        return "%.1f h" % (minutes / 60)
    return "%.1f days" % (minutes / 1440)


# ---- drawing -----------------------------------------------------------------------
def chart(beats, every, now, label):
    """Battery % over the last DAYS days. Breaks the line where heartbeats stopped."""
    W, H, L, R, T, B = 320, 96, 30, 6, 8, 20
    start = now - timedelta(days=DAYS)
    pts = [b for b in beats if b["t"] >= start and b.get("pct") is not None]
    if not pts:
        return '<p class="muted">No battery readings in the last %d days.</p>' % DAYS
    x = lambda t: L + (W - L - R) * (t - start).total_seconds() / (DAYS * 86400)
    y = lambda p: T + (H - T - B) * (1 - max(0, min(100, p)) / 100)
    out = ['<svg viewBox="0 0 %d %d" role="img" aria-label="%s battery, last %d days">' % (W, H, html.escape(label), DAYS)]
    for p in (0, 50, 100):                    # hairline grid + labels
        out.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (L, W - R, y(p), y(p)))
        out.append('<text class="axis" x="%d" y="%.1f" text-anchor="end" dy="3">%d%%</text>' % (L - 5, y(p), p))
    day = start.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    while day < now:                          # day ticks
        out.append('<text class="axis" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (x(day), H - 5, day.strftime("%a")))
        day += timedelta(days=1)
    seg, segs = [], []
    for b in pts:                             # gap > 3 intervals = outage, so lift the pen
        if seg and (b["t"] - seg[-1]["t"]) > timedelta(minutes=every * 3):
            segs.append(seg)
            seg = []
        seg.append(b)
    segs.append(seg)
    for s in segs:
        if len(s) == 1:
            out.append('<circle class="dot" cx="%.1f" cy="%.1f" r="2"/>' % (x(s[0]["t"]), y(s[0]["pct"])))
        else:
            out.append('<polyline class="line" points="%s"/>' % " ".join("%.1f,%.1f" % (x(b["t"]), y(b["pct"])) for b in s))
    for b in pts:                             # hover targets, bigger than the line
        tip = "%s · %d%%" % (b["t"].astimezone(TZ).strftime("%a %H:%M"), b["pct"])
        if b.get("v"):
            tip += " · %.2f V" % b["v"]
        out.append('<circle class="hit" cx="%.1f" cy="%.1f" r="6"><title>%s</title></circle>' % (x(b["t"]), y(b["pct"]), tip))
    out.append("</svg>")
    return "".join(out)


def card(name, beats, now):
    cfg = DEVICES.get(name, {})
    label, every = cfg.get("label", name), cfg.get("every", 30)
    last = beats[-1]
    state, notes, age, uptime = health(beats, every, now)
    word = {GOOD: "OK", WARN: "Check", SERIOUS: "Attention", CRIT: "Problem"}[state]
    if last.get("usb"):
        battery, sub = "USB", "mains power"
    elif last.get("pct") is not None:
        battery = "%d%%" % last["pct"]
        sub = ("%.2f V" % last["v"]) if last.get("v") else ("charging" if last.get("charging") else "")
    else:
        battery, sub = "–", ""
    facts = [("Last seen", "%s ago · %s" % (ago(age), last["t"].astimezone(TZ).strftime("%a %H:%M"))),
             ("Heartbeats, last 24 h", "%d%% of expected (every %d min)" % (uptime, every))]
    if last.get("place"):
        facts.append(("Place", last["place"]))
    if last.get("rssi") is not None:
        facts.append(("Wi-Fi", "%d dBm" % last["rssi"]))
    if last.get("awake") is not None:
        facts.append(("Awake per wake", "%.1f s" % (last["awake"] / 1000)))
    if last.get("version"):
        facts.append(("Version", last["version"]))
    rows = []
    for b in reversed(beats[-12:]):
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            b["t"].astimezone(TZ).strftime("%a %H:%M"),
            "USB" if b.get("usb") else ("–" if b.get("pct") is None else "%d%%" % b["pct"]),
            "%.2f" % b["v"] if b.get("v") else "",
            ("ok" if b["ok"] else "failed") + (" (%d)" % b["fails"] if b.get("fails") else ""),
            html.escape(b["ip"])))
    return """
<section class="card">
  <header><h2>{label}</h2><span class="status {state}"><span aria-hidden="true">{icon}</span> {word}</span></header>
  {notes}
  <div class="hero"><span class="value">{battery}</span><span class="sub">{sub}</span></div>
  {chart}
  <dl>{facts}</dl>
  <details><summary>Recent heartbeats</summary>
    <table><thead><tr><th>Time</th><th>Battery</th><th>Volts</th><th>Update</th><th>From</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </details>
</section>""".format(
        label=html.escape(label), state=state, icon=ICON[state], word=word,
        notes=('<p class="notes">%s</p>' % html.escape("; ".join(notes)[:1].upper() + "; ".join(notes)[1:])) if notes else "",
        battery=battery, sub=html.escape(sub), chart=chart(beats, every, now, label),
        facts="".join("<dt>%s</dt><dd>%s</dd>" % (html.escape(k), html.escape(str(v))) for k, v in facts),
        rows="".join(rows))


CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--ring:rgba(11,11,11,.10);--series:#2a78d6;--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
 --grid:#2c2c2a;--ring:rgba(255,255,255,.10);--series:#3987e5}}
body{margin:0;background:var(--page)}
.viz-root{font:15px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);max-width:720px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:4px 0 2px}.muted{color:var(--muted)}
.card{background:var(--surface);border-radius:12px;box-shadow:0 0 0 1px var(--ring);padding:14px 16px;margin:14px 0}
.card header{display:flex;justify-content:space-between;align-items:center}
h2{font-size:17px;margin:0}
.status{font-size:13px;font-weight:600;padding:2px 9px;border-radius:99px;color:#fff}
.status.good{background:var(--good)}.status.warning{background:var(--warning);color:#0b0b0b}
.status.serious{background:var(--serious)}.status.critical{background:var(--critical)}
.notes{margin:6px 0 0;color:var(--ink2)}
.hero{margin:10px 0 2px}.value{font-size:34px;font-weight:600;letter-spacing:-.5px}
.sub{color:var(--ink2);margin-left:8px}
svg{width:100%;height:auto;display:block;margin:4px 0 6px}
.grid{stroke:var(--grid);stroke-width:1}.axis{fill:var(--muted);font-size:10px}
.line{fill:none;stroke:var(--series);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.dot{fill:var(--series)}.hit{fill:transparent}.hit:hover{fill:var(--series);fill-opacity:.25}
dl{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;margin:8px 0}
dt{color:var(--ink2)}dd{margin:0}
summary{cursor:pointer;color:var(--ink2);margin-top:4px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}
th,td{text-align:left;padding:3px 6px;border-bottom:1px solid var(--grid)}th{color:var(--ink2);font-weight:600}
"""


def main():
    now = datetime.now(timezone.utc)
    beats = {}
    for dev, b in list(read_kindle(KINDLE_LOG)) + list(read_pico(PICO_LOG)):
        if b["t"] > now - timedelta(days=DAYS + 1):
            beats.setdefault(dev, []).append(b)
    for b in beats.values():
        b.sort(key=lambda r: r["t"])
    cards = "".join(card(d, beats[d], now) for d in sorted(beats))
    page = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>raindash status</title><style>{css}</style></head>
<body><main class="viz-root"><h1>raindash status</h1>
<p class="muted">Updated {when}. Refreshes every 5 minutes.</p>{cards}</main></body></html>""".format(
        css=CSS, when=now.astimezone(TZ).strftime("%a %d %b, %H:%M"),
        cards=cards or '<p>No heartbeats in the last %d days.</p>' % DAYS)
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(page)
    os.replace(tmp, OUT)                      # never serve a half-written page
    print("wrote %s (%d devices)" % (OUT, len(beats)))


if __name__ == "__main__":
    main()
