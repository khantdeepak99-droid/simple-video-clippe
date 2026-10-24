FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip

RUN pip install --no-cache-dir --pre --upgrade "yt-dlp[default]"

RUN pip install --no-cache-dir Flask gunicorn

COPY . .

RUN mkdir -p uploads outputs

CMD sh -c "gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 900 app:app"
