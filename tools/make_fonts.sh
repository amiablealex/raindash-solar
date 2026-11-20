#!/bin/sh
# Rebuild the bitmap fonts in firmware/lib from DejaVu Sans.
# Needs: pip install freetype-py; git clone https://github.com/peterhinch/micropython-font-to-py
#        mpy-cross matching the Pico's MicroPython version (pip install mpy-cross==1.29.*)
set -e
F=${FONT_TO_PY:-micropython-font-to-py/font_to_py.py}
D=${DEJAVU:-/usr/share/fonts/truetype/dejavu}
OUT=firmware/lib
python3 -c "open('/tmp/ascii_plus.txt','w',encoding='utf-8').write(''.join(map(chr,range(32,127)))+'·°−')"
python3 -c "open('/tmp/ascii.txt','w',encoding='utf-8').write(''.join(map(chr,range(32,127))))"
python3 -c "open('/tmp/temp.txt','w',encoding='utf-8').write('0123456789-−° ')"
mk() { python3 "$F" -x -k "/tmp/$4.txt" "$D/$1" "$2" "$OUT/font_$3.py" && mpy-cross "$OUT/font_$3.py" && rm "$OUT/font_$3.py"; }
mk DejaVuSans-Bold.ttf 22 b22 ascii_plus   # headline, 3 sizes (shrinks to fit)
mk DejaVuSans-Bold.ttf 18 b18 ascii_plus
mk DejaVuSans-Bold.ttf 15 b15 ascii_plus
mk DejaVuSans-Bold.ttf 42 b42 temp         # big temperature (digits only)
mk DejaVuSans.ttf      14 r14 ascii_plus   # status line
mk DejaVuSans.ttf      13 r13 ascii_plus   # small labels
mk DejaVuSans-Bold.ttf 13 b13 ascii        # "now", day names
