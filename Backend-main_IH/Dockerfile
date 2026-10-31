FROM python:3.11-slim

# Install system dependencies (build-essential, ffmpeg, libopus, curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libopus0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip & install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Environment settings
ENV PYTHONUNBUFFERED=1 \
    PORT=7860

EXPOSE 7860

CMD ["python", "bot.py"]
