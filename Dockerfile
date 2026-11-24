FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MUSIC_STREAM_DB=/data/music-stream.sqlite3
WORKDIR /app
RUN mkdir /data && chown 10001:10001 /data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY music_stream ./music_stream

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "music_stream.api:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
