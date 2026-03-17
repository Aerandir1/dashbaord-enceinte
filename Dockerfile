# ─── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# System deps needed to compile sounddevice / PortAudio
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Runtime deps only (PortAudio shared lib + pyopenssl for SSL adhoc)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# Non-root user for better security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 5000

# Use environment variables defined in .env / docker-compose
CMD ["python", "run.py"]
