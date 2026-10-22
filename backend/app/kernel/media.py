"""R3.5 — claim media: magic-byte typing, fail-closed metadata stripping (Pillow / pillow-heif for photos, the bundled ffmpeg
for videos), JPEG previews for HEIC and poster frames for videos. Nothing here trusts the file name or declared MIME type."""
from __future__ import annotations

import io
import os
import re
import subprocess

PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic")
VIDEO_TYPES = ("video/mp4", "video/quicktime")
MAX_PHOTO = 10 * 1024 * 1024
MAX_VIDEO = 100 * 1024 * 1024
MAX_FILES = 5
MAX_MESSAGE = 250 * 1024 * 1024
VIDEO_CODECS = ("h264", "hevc")
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic", "video/mp4": "mp4", "video/quicktime": "mov"}
_GPS_IFD = 0x8825
_LOCATION = re.compile(r"location|©xyz|\bxyz\b|gps|iso6709", re.I)
_KEEP_GLOBAL = {"major_brand", "minor_version", "compatible_brands", "encoder"}  # "encoder" is the ffmetadata writer's own tag


class Rejected(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def kind_of(content_type: str | None) -> str | None:
    return "photo" if content_type in PHOTO_TYPES else "video" if content_type in VIDEO_TYPES else None


def limit_for(content_type: str) -> int:
    return MAX_VIDEO if content_type in VIDEO_TYPES else MAX_PHOTO


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None
    return exe if exe and os.access(exe, os.X_OK) else None


def ffmpeg_version() -> str | None:
    exe = ffmpeg_exe()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"ffmpeg version (\S+)", out)
    return m.group(1) if m else None


def _heif():
    import pillow_heif
    pillow_heif.register_heif_opener()


def _no_gps(data: bytes) -> bool:
    from PIL import Image
    _heif()
    im = Image.open(io.BytesIO(data))
    ex = im.getexif()
    return not ex.get_ifd(_GPS_IFD) and _GPS_IFD not in ex and b"GPS" not in (im.info.get("xmp") or b"")


def strip_photo(data: bytes, content_type: str) -> bytes:
    """Re-encode without EXIF/XMP (drops GPS) and verify the result; any failure refuses the upload."""
    from PIL import Image, ImageOps
    _heif()
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        if content_type != "image/heic":
            im = ImageOps.exif_transpose(im)  # keep the photo upright once the orientation tag is gone
        im.info = {}
        out = io.BytesIO()
        if content_type == "image/heic":
            im.save(out, format="HEIF", quality=90, exif=None, xmp=None)
        else:
            fmt = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[content_type]
            if fmt == "JPEG" and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(out, format=fmt, exif=b"", **({"quality": 88} if fmt == "JPEG" else {}))
        clean = out.getvalue()
        ok = _no_gps(clean)
    except Exception as e:  # noqa: BLE001
        raise Rejected("metadata_strip_failed", "We couldn't remove location data from this photo, so it wasn't uploaded.") from e
    if not ok:
        raise Rejected("metadata_strip_failed", "We couldn't remove location data from this photo, so it wasn't uploaded.")
    return clean


def jpeg_preview(data: bytes, size: int = 1600) -> bytes:
    from PIL import Image
    _heif()
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    im.thumbnail((size, size))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=82, exif=b"")
    return out.getvalue()


def _run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=timeout)


def probe(path: str) -> dict:
    """Parse ffmpeg's own stream report (imageio-ffmpeg bundles ffmpeg only, no ffprobe)."""
    exe = ffmpeg_exe()
    if not exe:
        raise Rejected("video_unavailable", "Video uploads are switched off right now. Send photos, or try again later.")
    err = _run([exe, "-hide_banner", "-i", path], 60).stderr.decode("utf-8", "replace")
    meta = _run([exe, "-v", "error", "-i", path, "-f", "ffmetadata", "-"], 60).stdout.decode("utf-8", "replace")
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return {"report": err, "video": re.findall(r"Stream #\S+.*?: Video: (\w+)", err), "data": "Data:" in err,
            "duration_s": (int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3])) if d else None,
            "global_keys": {ln.split("=", 1)[0] for ln in meta.splitlines() if "=" in ln and not ln.startswith(";")},
            "metadata_text": meta}


def strip_video(src: str, dst: str, poster: str) -> dict:
    """Stream-copy into a fresh container with all metadata dropped, re-check, then grab a poster frame. Fail closed."""
    info = probe(src)
    if not info["video"]:
        raise Rejected("unsupported_type", "This doesn't look like a playable video. Send an MP4 or MOV from your phone.")
    if info["video"][0] not in VIDEO_CODECS:
        raise Rejected("unsupported_type", "This video format isn't supported. Send an H.264 or HEVC video (the usual phone format).")
    exe = ffmpeg_exe()
    r = _run([exe, "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "-1", "-map_metadata:s:v", "-1",
              "-map_metadata:s:a", "-1", "-map_chapters", "-1", "-c", "copy", "-fflags", "+bitexact", "-flags:v", "+bitexact",
              "-flags:a", "+bitexact", "-movflags", "+faststart", dst])
    fail = Rejected("metadata_strip_failed", "We couldn't remove location data from this video, so it wasn't uploaded.")
    if r.returncode != 0 or not os.path.exists(dst) or not os.path.getsize(dst):
        raise fail
    after = probe(dst)
    if (after["data"] or not after["video"] or after["global_keys"] - _KEEP_GLOBAL
            or _LOCATION.search(after["metadata_text"]) or _LOCATION.search(after["report"].split("Input #0", 1)[-1])):
        raise fail
    p = _run([exe, "-v", "error", "-y", "-ss", "0", "-i", dst, "-frames:v", "1", "-vf", "scale='min(640,iw)':-2",
              "-map_metadata", "-1", "-q:v", "4", poster], 60)
    if p.returncode != 0 or not os.path.exists(poster):
        raise fail
    return {"duration_s": round(after["duration_s"], 1) if after["duration_s"] else None, "codec": after["video"][0]}
