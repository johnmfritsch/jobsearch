# Playwright URL resolution microservice
# Base image includes Chromium + all required system dependencies pre-installed
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app

# Install Python packages first (before copying code — better layer caching)
RUN pip install --no-cache-dir playwright playwright-stealth && \
    python -m playwright install chromium

# Copy service script (changes here won't invalidate the pip layer above)
COPY server.py .


CMD ["python", "server.py"]
