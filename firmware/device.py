# device.py - the "product" side: battery voltage, USB detection, the TPL5110
# power timer, and a small state file that survives the power being cut.
import json
import time
from machine import Pin, ADC

DONE_PIN = 22          # -> TPL5110 DONE
USB_V = 4.25           # VSYS above this = USB power (a PC port gave 4.38 V; a battery after the diode ~4.0 V max)
STATE_FILE = "state.json"

# Rough Li-ion charge from voltage, measured lightly loaded. Good to ~10%.
CURVE = ((3.30, 0), (3.50, 5), (3.60, 12), (3.65, 20), (3.70, 30), (3.75, 40),
         (3.80, 50), (3.90, 65), (4.00, 80), (4.10, 90), (4.20, 100))


def vsys():
    """VSYS in volts. On a Pico W the VSYS sense pin (GP29) is shared with the
    Wi-Fi chip, so call this while Wi-Fi is off - raindash reads it before Wi-Fi starts."""
    Pin(25, Pin.OUT, value=1)                 # keep the Wi-Fi chip deselected
    Pin(29, Pin.IN, pull=None)
    adc = ADC(29)
    total = 0
    for _ in range(16):
        total += adc.read_u16()
    Pin(29, Pin.ALT, pull=Pin.PULL_DOWN, alt=7)   # hand the pin back to the Wi-Fi chip
    return total / 16 * 3 * 3.3 / 65535           # VSYS is divided by 3 on the board


def usb_power():
    """True when the Pico's own USB socket has power. On a Pico W the VBUS sense
    line is on the Wi-Fi chip (WL_GPIO2) - more trustworthy than guessing from VSYS."""
    try:
        return Pin("WL_GPIO2", Pin.IN).value() == 1
    except Exception:
        return False


def percent(v):
    if v <= CURVE[0][0]:
        return 0
    for (v0, p0), (v1, p1) in zip(CURVE, CURVE[1:]):
        if v <= v1:
            return int(p0 + (p1 - p0) * (v - v0) / (v1 - v0))
    return 100


def power(cfg):
    """{'usb': bool, 'v': battery volts or None, 'pct': 0-100 or None}"""
    bcfg = cfg.get("battery", {})
    v = vsys() * bcfg.get("vsys_scale", 1.0)   # calibrate against a multimeter, see CONFIG.md
    vbus = usb_power()
    if vbus or v > USB_V:
        return {"usb": True, "v": None, "pct": None, "vsys": v, "vbus": vbus}
    # the Schottky diode between the battery and VSYS drops ~0.2 V: add it back
    bat = v + bcfg.get("diode_v", 0.3)
    return {"usb": False, "v": bat, "pct": percent(bat), "vsys": v, "vbus": vbus}


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    with open(STATE_FILE, "w") as f:
        json.dump(st, f)


def power_off(sleep_s):
    """Tell the TPL5110 we're done, so it cuts our power until its next tick.
    If we're still running a moment later there is no working timer, so behave
    the same way without it: sleep, then restart from scratch (just uses more power).
    Never sleeps while USB is connected: sleeping switches the USB link off."""
    import machine
    if usb_power():
        print("USB connected: not sleeping, restarting in USB mode")
        time.sleep_ms(500)
        machine.reset()
    done = Pin(DONE_PIN, Pin.OUT, value=0)
    for _ in range(10):                       # a few clean rising edges
        done(1); time.sleep_ms(2)
        done(0); time.sleep_ms(2)
    done(1)
    time.sleep_ms(1000)
    done(0)
    print("still powered: no power timer, sleeping %d s instead" % sleep_s)
    # Sleep a minute at a time so plugging in USB is noticed within a minute
    # (USB doesn't wake a sleeping Pico, and lightsleep switches its USB off).
    for _ in range(max(1, sleep_s // 60)):
        machine.lightsleep(60000)
        if usb_power():
            break
    machine.reset()
