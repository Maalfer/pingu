FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (Pillow, yt-dlp need these)
RUN apt-get update && apt-get install -y     ffmpeg     libwebp-dev     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create runtime directories
RUN mkdir -p data uploads static/avatars static/icons

# Expose the port uvicorn listens on
EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
