# Earth Information Jukebox: sonification backend (FastAPI + music engine + audio rendering)
#
# Build:  docker build -t earth-jukebox-sonification .
#         docker build -t earth-jukebox-sonification --build-arg WITH_VSCO=1 .   # + real orchestral samples (~440 MB)
# Run:    docker run -p 8000:8000 -v /path/to/datasets/data:/app/data:ro -v jukebox-cache:/app/cache earth-jukebox-sonification
#         then  http://localhost:8000/api/health  and  http://localhost:8000/docs  (the frontend's calls: backend/FRONTEND_API.md)
#
# How the pieces fit (see the `deployment` repository):
#   * the `datasets` image fills a volume with the team's RAW data (precipitation_rain_2000_2026/, temperature_anomaly_1880_2025/, ...),
#     mounted READ-ONLY at /app/data;
#   * at start-up backend/serve.py converts those folders, in the background, into one dataset per variable and keeps
#     them in the cache volume (/app/cache/_datasets). It is done once: later starts only rebuild what changed;
#   * a request can mix variables freely: each is read from its own dataset and they are lined up by date;
#   * the API answers immediately; GET /api/health says "preparing_data": true until the datasets are ready.

ARG PYTHON_VERSION=3.12

# ─────────────────────────────────────────────────────────────
# Stage 1: Python packages that may need a compiler
# tinysoundfont has a ready-made wheel for x86_64 only; on arm64 it is compiled here, so the final image needs no gcc
# ─────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS wheels

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# --no-deps: tinysoundfont lists pyaudio (live playback), which the server does not need
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels tinysoundfont

# ─────────────────────────────────────────────────────────────
# Stage 2: the sounds (needs git; only the files reach the final image)
# - GeneralUser GS SoundFont (~32 MB): always, otherwise there is no audio
# - VSCO 2 CE orchestral samples (~440 MB, CC0): only with --build-arg WITH_VSCO=1
# The project's own scripts do the download, so the image gets exactly what the code expects.
# ─────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS sounds

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir numpy soundfile
COPY music/ music/
COPY soundfonts/ soundfonts/

RUN python music/render_audio.py --download \
    && test -s soundfonts/GeneralUser-GS.sf2

# the recordings of the realistic mode (rain, fire, thunder, crickets ...; CC0 / public domain / CC BY, ~30 MB)
RUN python music/soundscape.py --download \
    && test -s soundfonts/ambience/rain_light.flac

ARG WITH_VSCO=0
RUN if [ "$WITH_VSCO" = "1" ]; then \
        python music/sampler.py --download \
        && python -c "import sys; sys.path.insert(0, 'music'); import sampler; sys.exit(0 if sampler.vsco_available() else 1)" \
        && rm -rf soundfonts/vsco/.git; \
    fi

# ─────────────────────────────────────────────────────────────
# Stage 3: the image that is deployed
# ─────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS production

# - ffmpeg: MP3 encoding          - libsndfile1: needed by the soundfile package
# - curl: container health check
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies first, for better layer caching
COPY requirements.txt .
COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps /wheels/*.whl \
    && rm -rf /wheels

# Application code (.dockerignore keeps the examples, caches and local data out)
COPY . .

# Sounds from stage 2
COPY --from=sounds /app/soundfonts/ soundfonts/

# Configuration (can be overridden at run time; the deployment's docker-compose sets the same values)
#   JUKEBOX_DATA_DIR  : the team's raw folders and/or ready-made <prefix>_<variable>.npz + .json (may be read-only)
#   JUKEBOX_CACHE_DIR : compositions + the converted datasets (must be writable; a volume keeps them between restarts)
#   JUKEBOX_PREPARE   : 0 = do not convert raw data at start-up
#   JUKEBOX_MERGE_DATASETS : 1 = each variable is read from its own dataset and they are lined up by date
#   JUKEBOX_FULL_API  : 0 = serve only the frontend's calls (/api/instruments, /api/genres, /api/sonify, /api/zones/zone-data, /api/zones/series, /api/zones/groove) and /api/health;
#                       1 = also the engine's own endpoints and http://localhost:8000/docs for them
ENV JUKEBOX_DATA_DIR=/app/data \
    JUKEBOX_CACHE_DIR=/app/cache \
    JUKEBOX_CORS=* \
    JUKEBOX_PREPARE=1     JUKEBOX_MERGE_DATASETS=1 \
    JUKEBOX_FULL_API=0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /app/data /app/cache

# Fail the build, not the deployment, if the image cannot make music or convert data
RUN python -c "import tinysoundfont, soundfile, fastapi, uvicorn, numpy; import shutil, sys; sys.exit(0 if shutil.which('ffmpeg') else 1)" \
    && test -s soundfonts/GeneralUser-GS.sf2 \
    && test -s simulation/land_mask_025deg.npz \
    && python simulation/build_monthly_dataset.py --help > /dev/null

EXPOSE 8000

# The API answers as soon as the server is up, also while the data is still being converted
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/api/health" || exit 1

# Converts the raw data in the background, then serves backend.app:app with uvicorn
CMD ["python", "backend/serve.py"]
