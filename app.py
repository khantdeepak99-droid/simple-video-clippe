import os
import re
import uuid
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, render_template, request, send_file, url_for, flash, redirect
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-before-production")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))  # 1 GiB
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
MAX_CLIP_SECONDS = int(os.environ.get("MAX_CLIP_SECONDS", "1800"))  # 30 minutes


class UserInputError(Exception):
    pass


def tool_exists(name):
    return shutil.which(name) is not None


def parse_timestamp(value, field_name):
    """Accept seconds, MM:SS, or HH:MM:SS; return a non-negative float."""
    value = (value or "").strip()
    if not value:
        raise UserInputError(f"Please enter the {field_name} timestamp.")
    try:
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            seconds = float(value)
        else:
            parts = value.split(":")
            if len(parts) not in (2, 3):
                raise ValueError
            if not all(re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts):
                raise ValueError
            numbers = [float(p) for p in parts]
            if len(numbers) == 2:
                if numbers[1] >= 60:
                    raise UserInputError(f"{field_name.capitalize()} timestamp: seconds must be less than 60 in MM:SS format.")
                seconds = numbers[0] * 60 + numbers[1]
            else:
                if numbers[1] >= 60 or numbers[2] >= 60:
                    raise UserInputError(f"{field_name.capitalize()} timestamp: minutes and seconds must be less than 60 in HH:MM:SS format.")
                seconds = numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    except ValueError:
        raise UserInputError(f"Invalid {field_name} timestamp. Use seconds, MM:SS, or HH:MM:SS.")
    if seconds < 0:
        raise UserInputError(f"{field_name.capitalize()} timestamp cannot be negative.")
    return seconds


def is_youtube_url(value):
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        return parsed.scheme in ("http", "https") and (
            host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
            or host == "youtube-nocookie.com" or host.endswith(".youtube-nocookie.com")
        )
    except Exception:
        return False


def run_command(args, timeout=900):
    try:
        result = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise UserInputError("Processing took too long and was stopped. Try a shorter clip or a smaller source video.")
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        # Keep errors useful without exposing an excessive log.
        details = details[-1800:]
        raise UserInputError("The video could not be processed. " + (details or "Please check the video and timestamps."))
    return result


def get_upload_source(file_storage, job_id):
    if not file_storage or not file_storage.filename:
        raise UserInputError("Choose a video file to upload.")
    safe_name = secure_filename(file_storage.filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UserInputError("Unsupported file type. Upload MP4, MOV, MKV, WEBM, M4V, or AVI.")
    path = UPLOAD_DIR / f"{job_id}_source{suffix}"
    file_storage.save(path)
    if not path.exists() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        raise UserInputError("The uploaded file is empty or could not be saved.")
    return path


def download_youtube_source(video_url, job_id):
    if not is_youtube_url(video_url):
        raise UserInputError("Enter a valid YouTube video URL (youtube.com or youtu.be).")
    if not tool_exists("yt-dlp"):
        raise UserInputError("YouTube support is not installed on this server. Install dependencies from requirements.txt and redeploy.")
    # yt-dlp must retrieve the media to the server temporarily; a video cannot be clipped
    # into a downloadable file without processing its media bytes.
    output_template = str(UPLOAD_DIR / f"{job_id}_youtube.%(ext)s")
    command = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--restrict-filenames",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", output_template,
        video_url.strip(),
    ]
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=900, check=False
        )
    except subprocess.TimeoutExpired:
        raise UserInputError("Retrieving this YouTube video took too long. Try another video or upload the video file directly.")
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        message = message[-1600:]
        raise UserInputError(
            "Could not retrieve this YouTube video. It may be private, age-restricted, region-blocked, unavailable, "
            "or not downloadable by the current yt-dlp version. You can try updating yt-dlp or upload a video file you have permission to use. "
            + message
        )
    candidates = sorted(
        [p for p in UPLOAD_DIR.glob(f"{job_id}_youtube.*") if p.is_file() and p.stat().st_size > 0],
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not candidates:
        raise UserInputError("YouTube retrieval finished, but no video file was created.")
    return candidates[0]


def create_clip(source_path, start, end, output_path):
    duration = end - start
    # Accurate seeking: place -ss after -i, then encode the requested interval.
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source_path),
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    run_command(command, timeout=900)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise UserInputError("FFmpeg did not produce a clip. Check that the start time is before the video's end.")


def cleanup_file(path):
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


@app.errorhandler(413)
def too_large(_error):
    flash("The uploaded file is too large for this server's configured upload limit.", "error")
    return redirect(url_for("index")), 413


@app.get("/")
def index():
    return render_template("index.html", max_clip_seconds=MAX_CLIP_SECONDS)


@app.post("/clip")
def clip():
    job_id = uuid.uuid4().hex
    source_path = None
    output_path = OUTPUT_DIR / f"clip_{job_id}.mp4"
    start_raw = request.form.get("start_time", "")
    end_raw = request.form.get("end_time", "")
    source_type = request.form.get("source_type", "upload")
    try:
        if not tool_exists("ffmpeg"):
            raise UserInputError("FFmpeg is not available on this server. Check the Dockerfile/deployment logs.")
        start = parse_timestamp(start_raw, "start")
        end = parse_timestamp(end_raw, "end")
        if end <= start:
            raise UserInputError("End time must be greater than start time.")
        if end - start > MAX_CLIP_SECONDS:
            raise UserInputError(f"Clip duration is limited to {MAX_CLIP_SECONDS // 60} minutes per request.")

        if source_type == "youtube":
            video_url = request.form.get("youtube_url", "").strip()
            source_path = download_youtube_source(video_url, job_id)
        else:
            source_path = get_upload_source(request.files.get("video_file"), job_id)

        create_clip(source_path, start, end, output_path)
        duration = end - start
        return render_template(
            "index.html",
            max_clip_seconds=MAX_CLIP_SECONDS,
            clip_ready=True,
            download_url=url_for("download_clip", filename=output_path.name),
            clip_duration=f"{int(duration // 60):02d}:{int(duration % 60):02d}",
            source_type=source_type,
        )
    except UserInputError as exc:
        cleanup_file(output_path)
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except Exception:
        cleanup_file(output_path)
        app.logger.exception("Unexpected error while creating a clip")
        flash("Something went wrong while creating the clip. Check the server logs and try again.", "error")
        return redirect(url_for("index"))
    finally:
        cleanup_file(source_path)
        # Clean up any leftover source variants for this job (e.g. yt-dlp sidecars).
        for leftover in UPLOAD_DIR.glob(f"{job_id}_*"):
            cleanup_file(leftover)


@app.get("/download/<filename>")
def download_clip(filename):
    # Only serve files matching the generated clip filename pattern.
    if not re.fullmatch(r"clip_[a-f0-9]{32}\.mp4", filename):
        return "Not found", 404
    path = OUTPUT_DIR / filename
    if not path.is_file():
        flash("That clip is no longer available. Please create it again.", "error")
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True, download_name="my_clip.mp4", mimetype="video/mp4")


@app.get("/health")
def health():
    return {"status": "ok", "ffmpeg": tool_exists("ffmpeg"), "yt_dlp": tool_exists("yt-dlp")}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
