FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app
RUN pip install --no-cache-dir playwright itsdangerous && python -m playwright install chromium
COPY server.py .

CMD ["python", "server.py"]
