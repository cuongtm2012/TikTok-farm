# TikTok Farm - Docker Image
# Python + Playwright Chromium for browser automation

FROM python:3.12-slim

WORKDIR /app

# Install system deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium
RUN playwright install chromium && playwright install-deps chromium

# Copy source code
COPY . .

# Use SQLite by default (no external DB needed)
ENV DATABASE_DRIVER=sqlite

# Expose port
EXPOSE 8080

# Run headless by default (no web server in test)
CMD ["python", "src/main.py", "--headless"]
