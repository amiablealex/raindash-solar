# tpltest.py - checks a TPL5110 power timer using the Pico (display unplugged, Pico on USB).
#
# Wiring (Pico physical pin numbers):
#   Pico pin 36 (3V3 OUT) -> TPL VDD
#   Pico pin 38 (GND)     -> TPL GND
#   TPL DRV               -> Pico pin 32 (GP27)
#   Pico pin 29 (GP22)    -> TPL DONE
#
# Open this file in Thonny and press Run. Every time the timer switches its
# output on, this prints how long since the last time, then sends DONE and
# checks the output switches off. Ctrl+C (or Stop) to finish.
from machine import Pin
import time

drv = Pin(27, Pin.IN, Pin.PULL_DOWN)
done = Pin(22, Pin.OUT, value=0)

print("Watching the TPL5110. Press its button, or wait for its timer...")
last = None
while True:
    while not drv():
        time.sleep_ms(1)
    now = time.ticks_ms()
    if last is None:
        print("Output ON")
    else:
        print("Output ON, %.1f s after the last time" % (time.ticks_diff(now, last) / 1000))
    last = now

    time.sleep_ms(300)                        # pretend to do some work
    done(1)
    t0 = time.ticks_ms()
    while drv() and time.ticks_diff(time.ticks_ms(), t0) < 500:
        pass
    if drv():
        print("  sent DONE, output still ON  -> DONE isn't working (check the DONE wire)")
        while drv():
            time.sleep_ms(1)
        print("  output went OFF by itself")
    else:
        print("  sent DONE, output OFF  -> good")
    done(0)
