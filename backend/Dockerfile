# ==============================================================================
# Autonomous Job Finder & Auto-Apply Backend + Playwright Worker Dockerfile
# Uses official Microsoft Playwright Python image with pre-installed Chromium
# and system dependencies for deterministic headless form submission.
# ==============================================================================
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install Python dependencies
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/backend/requirements.txt && \
    playwright install chromium

# Copy backend source code
COPY backend /app/backend

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
