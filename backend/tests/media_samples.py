"""R3.5 test samples: location-tagged JPEG/HEIC and a location-tagged MP4/MOV built on the fly (no binary fixtures)."""
from __future__ import annotations

import io
import os
import subprocess
import tempfile

from app.kernel import media


def _gps_exif():
    from PIL import Image
    ex = Image.Exif()
    ex[0x010F] = "TestCam"
    ex[media._GPS_IFD] = {1: "S", 2: (33.0, 51.0, 54.0), 3: "E", 4: (151.0, 12.0, 34.0)}
    return ex


def photo(fmt: str = "JPEG", gps: bool = True) -> bytes:
    from PIL import Image
    media._heif()
    im = Image.new("RGB", (64, 48), (180, 40, 40))
    out = io.BytesIO()
    im.save(out, format=fmt, **({"exif": _gps_exif().tobytes()} if gps else {}))
    return out.getvalue()


def video(ext: str = "mp4", seconds: int = 1) -> bytes:
    exe = media.ffmpeg_exe()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, f"v.{ext}")
        subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={seconds}",
                        "-c:v", "libx264" if ext == "mp4" else "mpeg4", "-pix_fmt", "yuv420p",
                        "-metadata", "location=-33.8650+151.2094/", "-metadata", "com.apple.quicktime.location.ISO6709=-33.8650+151.2094/",
                        "-metadata", "title=Sydney test", *(["-c:v", "libx264"] if ext == "mov" else []), p], check=True, capture_output=True)
        return open(p, "rb").read()


def video_has_location(data: bytes, ext: str = "mp4") -> bool:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, f"x.{ext}")
        open(p, "wb").write(data)
        info = media.probe(p)
        return bool(media._LOCATION.search(info["metadata_text"]) or media._LOCATION.search(info["report"].split("Input #0", 1)[-1]))
