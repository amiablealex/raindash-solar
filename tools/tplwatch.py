# tplwatch.py - logs every change on the TPL5110's DRV pin, without ever sending DONE.
# Same wiring as tpltest.py. With no DONE, the timer still does something at the
# end of every interval, so the gaps between changes show the interval itself.
from machine import Pin
import time

done = Pin(22, Pin.OUT, value=0)     # hold DONE low: we never send it
drv = Pin(27, Pin.IN, Pin.PULL_DOWN)
events = []


def edge(p):
    events.append((time.ticks_ms(), p.value()))


t0 = time.ticks_ms()
drv.irq(edge, Pin.IRQ_RISING | Pin.IRQ_FALLING)
print("DRV is %s right now. Logging every change (Stop to finish)..." % ("HIGH" if drv() else "LOW"))
last = None
while True:
    while events:
        t, v = events.pop(0)
        gap = "" if last is None else "   (+%.1f s)" % (time.ticks_diff(t, last) / 1000)
        print("%8.1f s  DRV %s%s" % (time.ticks_diff(t, t0) / 1000, "HIGH" if v else "LOW", gap))
        last = t
    time.sleep_ms(20)
