# Earth Information Jukebox: Sonification Backend
# Python 3.12 with audio processing dependencies
# Build with: docker build -t earth-jukebox-sonification .
# Run with:   docker run -p 8000:8000 -v jukebox-data:/app/data earth-jukebox-sonification

FROM python:3.12-slim AS production

# Install system dependencies for audio processing and scientific computing
# - ffmpeg: audio encoding/decoding (MP3, WAV)
# - libsndfile1: soundfile library dependency
# - libnetcdf19: NetCDF file support for climate data
# - curl: health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    libnetcdf19 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Environment configuration (can be overridden at runtime)
ENV JUKEBOX_DATA_DIR=/app/data
ENV JUKEBOX_CACHE_DIR=/app/cache
ENV JUKEBOX_CORS=*

# Create directories for data and cache
RUN mkdir -p /app/data /app/cache

# Expose API port
EXPOSE 8000

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Run uvicorn server
# The app is at backend/app.py, module path is backend.app:app
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
