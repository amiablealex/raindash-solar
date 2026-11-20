# Hardware

## Parts

| Part | Notes |
|------|-------|
| Raspberry Pi Pico W | |
| Waveshare Pico-ePaper-3.7 | 480×280, 4 greys. The Pico plugs into its header |
| 18650 Li-ion cell + holder | |
| TP4056 charger module, USB-C | Must be the protected type: six pads (IN±, B±, OUT±) and a DW01 chip |
| Adafruit TPL5110 power timer | Cut the "Trim Enable" jumper and fit a fixed timing resistor |
| 100 kΩ + 1 MΩ resistors | In parallel, Delay to GND: ~29 min between updates. 91 kΩ ≈ 29 min, 100 kΩ ≈ 36 min |
| 2 × Schottky diode (1N5819) | Not 1N4007: a silicon diode loses ~0.8 V |
| Solar panel, 6 V, ~1 W | About 110 × 60 mm |
| Rocker or slide switch | Battery isolation for storage and transport |
| JST-PH 2-pin connectors | So the battery and panel can be unplugged |

## Wiring

```
Solar + ──►|── TP4056 IN+          Solar − ── TP4056 IN−
Cell + ── TP4056 B+                Cell − ── TP4056 B−
TP4056 OUT+ ── switch ── TPL5110 VDD
TPL5110 DRV ──►|── Pico VSYS (pin 39)      (display power comes from VSYS too)
TPL5110 DONE ── Pico GP22 (pin 29)
TPL5110 GND, TP4056 OUT− ── Pico GND
TPL5110 Delay ── 100k ∥ 1M ── GND

──►|── = Schottky diode, stripe towards the arrow
```

The display uses GP8–GP13 (DC, CS, SCK, MOSI, RST, BUSY). The firmware reads the battery from VSYS via the Pico W's ADC (GP29) and detects USB from the Wi-Fi chip's VBUS line.

The diode before VSYS stops USB power reaching the cell when the Pico is plugged into a computer. The switch cuts power to the Pico only; the cell still charges with it off.

## Calibration

The battery reading is VSYS plus the voltage lost on the way (diode, timer switch, wiring). Measure the cell with a multimeter, compare it with the heartbeat's `battery_v`, and set `battery.diode_v` in `config.json` to match. About 0.3–0.4 V is typical with a Schottky.

## Enclosure

Coming soon: 3D-printed, PETG or ASA rather than PLA, because a sunny window sill gets hot.
