# Simple Clipper

A minimal Flask web app that creates a downloadable MP4 clip from manually entered start and end timestamps. Input can be an uploaded video or a YouTube URL.

## Important note about YouTube links

A downloadable clip cannot be made from a YouTube URL without retrieving and processing the underlying media data. This app temporarily retrieves the source on the server using `yt-dlp`, trims it with FFmpeg, and removes the temporary source afterward. It does **not** save the entire source video for the user to download. Some videos will fail because they are private, restricted, unavailable, or because YouTube changes its delivery system. Use only videos you are authorized to access and process, and comply with YouTube's terms and applicable copyright law.

## Requirements

- Python 3.11+ recommended
- FFmpeg installed and available in `PATH`
- `yt-dlp` is installed automatically from `requirements.txt`

## Run locally (Windows PowerShell)

1. Open a terminal in this project folder.
2. Create and activate a virtual environment (recommended):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install Python dependencies:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. Confirm FFmpeg is available:

   ```powershell
   ffmpeg -version
   ```

5. Start the app:

   ```powershell
   python app.py
   ```

6. Open `http://127.0.0.1:5000`.

## Timestamp formats

Accepted: `HH:MM:SS`, `MM:SS`, or seconds such as `85` or `85.5`. End time must be later than start time. The default maximum clip length is 30 minutes and can be changed using `MAX_CLIP_SECONDS`.

## Deploy on Render

This project includes a Dockerfile. Create a new **Web Service** in Render and connect this project repository, then choose **Docker** as the runtime. No build command is required; the Dockerfile installs FFmpeg and Python dependencies. Render supplies the `PORT` environment variable.

Recommended environment variables:
- `SECRET_KEY`: set a long random value
- `MAX_CLIP_SECONDS`: `1800` (optional)
- `MAX_UPLOAD_BYTES`: `1073741824` (optional; 1 GiB)

### Hosting limitations

Video processing consumes CPU, memory, bandwidth, and disk space. A small/free host may time out, sleep, or run out of temporary storage for large videos. For larger videos, use a suitable paid instance. The app currently processes one request synchronously and stores generated clips in its local `outputs/` directory; ephemeral hosting storage may be cleared on restart or redeploy.

## Security / operational notes

- YouTube URLs are restricted to YouTube domains.
- FFmpeg and yt-dlp are invoked without a shell.
- Uploaded filenames are replaced with generated names.
- Only generated clip filenames can be downloaded through the download route.
- Source files are removed after processing; generated clips remain until the host clears them or the app is restarted.
- For public production use, add authentication/rate limiting and automatic expiry/cleanup for output clips.

## Health check

Visit `/health` to see whether FFmpeg and yt-dlp are available.
