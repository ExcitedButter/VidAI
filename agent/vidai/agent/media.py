"""ffmpeg / ffprobe helpers shared by generation, QC and assembly."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def ffprobe_metadata(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr[:300]}")
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 0.0
    if video.get("avg_frame_rate") and "/" in video["avg_frame_rate"]:
        num, den = video["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    return {
        "duration_s": float(data.get("format", {}).get("duration") or video.get("duration") or 0.0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "has_audio": audio is not None,
    }


def extract_frames(path: Path, out_dir: Path, num_frames: int, duration_s: float) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[str] = []
    duration_s = max(duration_s, 0.2)
    for index in range(num_frames):
        timestamp = duration_s * (index + 0.5) / num_frames
        frame_path = out_dir / f"frame_{index:02d}.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(path),
             "-frames:v", "1", "-q:v", "3", str(frame_path)],
            capture_output=True, text=True,
        )
        if proc.returncode == 0 and frame_path.is_file():
            frames.append(str(frame_path))
    return frames


_RATIO_SIZE = {"9:16": (720, 1280), "16:9": (1280, 720), "3:4": (720, 960), "4:3": (960, 720),
               "1:1": (960, 960), "adaptive": (720, 1280)}


def normalize_clip(src: Path, dst: Path, aspect_ratio: str, trim_s: float | None = None) -> Path:
    """Re-encode a clip to a uniform size / fps / codec with an audio track (silence if none)."""
    width, height = _RATIO_SIZE.get(aspect_ratio, _RATIO_SIZE["9:16"])
    meta = ffprobe_metadata(src)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if not meta["has_audio"]:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p")
    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    if not meta["has_audio"]:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    if trim_s:
        cmd += ["-t", f"{trim_s:.3f}"]
    cmd += ["-movflags", "+faststart", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"normalize failed for {src}: {proc.stderr[:400]}")
    return dst


def concat_clips(clips: list[Path], dst: Path) -> Path:
    """Concatenate already-normalized clips (jump cuts = plain hard cuts)."""
    list_path = dst.with_suffix(".txt")
    list_path.write_text("".join(f"file '{p.resolve()}'\n" for p in clips), encoding="utf-8")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-c", "copy", "-movflags", "+faststart", str(dst)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"concat failed: {proc.stderr[:400]}")
    return dst


def audio_mean_volume_db(path: Path) -> float | None:
    """Mean loudness of the audio track in dBFS via ffmpeg volumedetect; None when it cannot be measured."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    for line in (proc.stderr or "").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except ValueError:
                return None
    return None


def compose_on_canvas(src: Path, dst: Path, size: tuple[int, int], fill: float = 0.78) -> Path:
    """Place a product photo (any aspect, RGBA ok) centered on a neutral canvas of `size` (w, h)."""
    from PIL import Image, ImageFilter

    width, height = size
    image = Image.open(src).convert("RGBA")
    scale = min(width * fill / image.width, height * fill / image.height)
    product = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)
    # background: blurred, brightened version of the photo itself so the cutaway feels lit like a set
    background = image.convert("RGB").resize((width, height), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    background = Image.blend(background, Image.new("RGB", (width, height), (243, 241, 236)), 0.65)
    x = (width - product.width) // 2
    y = max(0, int(height * 0.46 - product.height / 2))
    background.paste(product, (x, y), product)
    dst.parent.mkdir(parents=True, exist_ok=True)
    background.save(dst, "JPEG", quality=92)
    return dst
