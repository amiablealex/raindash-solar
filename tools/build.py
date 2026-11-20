"""Builds dist/pico/ (everything that goes on the Pico) and a release zip.

    pip install "mpy-cross==1.29.*"     # must match the Pico's MicroPython version
    python tools/build.py

raindash.py is precompiled to .mpy because compiling it on the Pico needs more
RAM than the Pico has to spare.
"""
import os
import re
import shutil
import subprocess
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "firmware")
OUT = os.path.join(ROOT, "dist", "pico")
MPY_CROSS = os.environ.get("MPY_CROSS", "mpy-cross")

shutil.rmtree(os.path.join(ROOT, "dist"), ignore_errors=True)
shutil.copytree(os.path.join(SRC, "lib"), os.path.join(OUT, "lib"))
for f in ("main.py", "epd.py", "device.py", "config.example.json"):
    shutil.copy(os.path.join(SRC, f), OUT)
subprocess.run([MPY_CROSS, "-o", os.path.join(OUT, "raindash.mpy"), os.path.join(SRC, "raindash.py")], check=True)

version = re.search(r'^VERSION = "(.*)"', open(os.path.join(SRC, "raindash.py")).read(), re.M).group(1)
zip_path = os.path.join(ROOT, "dist", "raindash-solar-%s.zip" % version)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for folder, _, files in os.walk(OUT):
        for f in files:
            full = os.path.join(folder, f)
            z.write(full, os.path.relpath(full, os.path.dirname(OUT)))
print(zip_path)
