# epd.py - lean driver for the Waveshare Pico-ePaper-3.7 (480x280, 4 greys).
#
# Based on Waveshare's Pico_ePaper-3.7.py (MIT licence, (c) Waveshare team):
# same init sequence and look-up table, but
#   - no screen clear on start-up (the 4-grey waveform flashes the panel anyway)
#   - the image is converted with @viper code and sent in one SPI write per
#     plane, instead of byte-by-byte in pure Python (~25 s saved per refresh)
#   - you draw on a landscape 480x280 framebuffer; rotation happens on send
#
# Colours (2 bits per pixel): BLACK 0, DARK 2, LIGHT 1, WHITE 3.
from machine import Pin, SPI
import framebuf
import micropython
import time

W, H = 480, 280                  # landscape, as we draw it
PW, PH = 280, 480                # the panel's native portrait layout
BLACK, LIGHT, DARK, WHITE = 0, 1, 2, 3

RST_PIN, DC_PIN, CS_PIN, BUSY_PIN = 12, 8, 9, 13
CHUNK_ROWS = 48                  # portrait rows converted + sent per SPI write

LUT_4GRAY_GC = bytes([
    0x2A, 0x06, 0x15, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x28, 0x06, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x20, 0x06, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x14, 0x06, 0x28, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x02, 0x02, 0x0A, 0x00, 0x00, 0x00, 0x08, 0x08, 0x02,
    0x00, 0x02, 0x02, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x22, 0x22, 0x22, 0x22, 0x22,
])


@micropython.viper
def _plane(src: ptr8, dst: ptr8, bit: int, rot: int, py0: int, rows: int):
    # Rotate part of the landscape GS2_HMSB buffer (120 bytes/row) into portrait
    # rows py0..py0+rows of one 1-bit plane (35 bytes/row, MSB = leftmost pixel).
    # bit 1 -> the "high" plane (RAM 0x24), bit 0 -> the "low" plane (RAM 0x26).
    py = py0
    while py < py0 + rows:
        bx = 0
        while bx < 35:
            out = 0
            k = 0
            while k < 8:
                px = bx * 8 + k
                if rot == 90:            # panel's ribbon on the right
                    lx = py
                    ly = 279 - px
                else:                    # 270: ribbon on the left
                    lx = 479 - py
                    ly = px
                v = src[ly * 120 + (lx >> 2)] >> ((lx & 3) << 1)
                out = (out << 1) | ((v >> bit) & 1)
                k += 1
            dst[(py - py0) * 35 + bx] = out
            bx += 1
        py += 1


class EPD:
    def __init__(self, rotate=90):
        self.rotate = rotate
        # SPI FIRST: SPI1 claims GP8 as its default MISO, and GP8 is our DC pin.
        # Creating the Pin objects afterwards hands GP8 back to us as a plain output.
        self.spi = SPI(1, baudrate=4_000_000)    # GP10 SCK, GP11 MOSI
        self.rst = Pin(RST_PIN, Pin.OUT)
        self.dc = Pin(DC_PIN, Pin.OUT)
        self.cs = Pin(CS_PIN, Pin.OUT, value=1)
        self.busy = Pin(BUSY_PIN, Pin.IN, Pin.PULL_UP)
        self.buf = bytearray(W * H // 4)          # 33.6 KB drawing buffer
        self.fb = framebuf.FrameBuffer(self.buf, W, H, framebuf.GS2_HMSB)
        self.chunk = bytearray(35 * CHUNK_ROWS)   # 1.7 KB; the planes are sent in pieces

    # --- low level -----------------------------------------------------------
    def _cmd(self, c, data=None):
        self.dc(0)
        self.cs(0)
        self.spi.write(bytes((c,)))
        self.cs(1)
        if data is not None:
            self._data(data)

    def _data(self, data):
        self.dc(1)
        self.cs(0)
        self.spi.write(data)
        self.cs(1)

    def _wait(self, timeout_ms=15000):
        t0 = time.ticks_ms()
        # BUSY only goes high a moment after a command. Checking straight away can
        # see "idle" before the panel has even started, so give it time to rise.
        while self.busy() == 0 and time.ticks_diff(time.ticks_ms(), t0) < 50:
            time.sleep_ms(1)
        while self.busy() == 1:                   # 1 = busy
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                raise OSError("e-paper busy timeout")
            time.sleep_ms(10)

    # --- public --------------------------------------------------------------
    def init(self):
        self.rst(1); time.sleep_ms(30)
        self.rst(0); time.sleep_ms(3)
        self.rst(1); time.sleep_ms(30)
        self._cmd(0x12)                           # software reset
        time.sleep_ms(300)
        self._cmd(0x46, b"\xF7"); self._wait()    # auto-write RAM patterns
        self._cmd(0x47, b"\xF7"); self._wait()
        self._cmd(0x01, b"\xDF\x01\x00")          # gate number
        self._cmd(0x03, b"\x00")                  # gate voltage
        self._cmd(0x04, b"\x41\xA8\x32")          # source voltage
        self._cmd(0x11, b"\x03")                  # data entry sequence
        self._cmd(0x3C, b"\x03")                  # border
        self._cmd(0x0C, b"\xAE\xC7\xC3\xC0\xC0")  # booster strength
        self._cmd(0x18, b"\x80")                  # internal temperature sensor
        self._cmd(0x2C, b"\x44")                  # VCOM
        self._cmd(0x37, bytes(10))                # display options
        self._cmd(0x44, b"\x00\x00\x17\x01")      # RAM X range
        self._cmd(0x45, b"\x00\x00\xDF\x01")      # RAM Y range
        self._cmd(0x22, b"\xCF")

    def show(self):
        """Send the drawing buffer and do one full 4-grey refresh (~3 s)."""
        self._cmd(0x49, b"\x00")
        for ram, bit in ((0x24, 1), (0x26, 0)):
            self._cmd(0x4E, b"\x00\x00")
            self._cmd(0x4F, b"\x00\x00")
            self._cmd(ram)
            for py0 in range(0, PH, CHUNK_ROWS):   # RAM address auto-increments
                _plane(self.buf, self.chunk, bit, self.rotate, py0, CHUNK_ROWS)
                self._data(self.chunk)
        self._cmd(0x32, LUT_4GRAY_GC)
        self._cmd(0x22, b"\xC7")
        self._cmd(0x20)                           # go
        t0 = time.ticks_ms()
        self._wait()
        self.refresh_ms = time.ticks_diff(time.ticks_ms(), t0)   # for power measurements

    def sleep(self):
        """Deep sleep - always do this after a refresh. Needs init() to wake."""
        self._cmd(0x10, b"\x03")
