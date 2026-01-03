# =============================================================================
# COMMERCIAL CURATOR - PRODUCTION DOCKERFILE
# =============================================================================
# Multi-stage build for minimal image size
# Agent 0: Firecrawl → Qdrant pipeline
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1: Builder
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: Production
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim as production

WORKDIR /app

# Security: Run as non-root user
RUN groupadd -r curator && useradd -r -g curator curator

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY --chown=curator:curator . .

# Set environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8100 \
    LOG_LEVEL=INFO

# Expose port
EXPOSE 8100

# Switch to non-root user
USER curator

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8100/health').raise_for_status()"

# Run the application
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
