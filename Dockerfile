# TikTok Farm - Docker Image (Production)
# Uses plain Playwright Chromium (stable) instead of Camoufox to avoid crash issues
# FIXED v2: Stable Chromium, no Camoufox container mismatch

FROM python:3.12-slim

WORKDIR /app

# Install system deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium (stable, no Camoufox)
RUN playwright install chromium && playwright install-deps chromium

# Copy source code
COPY . .

# Use SQLite by default
ENV DATABASE_DRIVER=sqlite
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8080

# Run with web server (uvicorn handles signals gracefully)
CMD ["python", "src/main.py"]
