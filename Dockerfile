FROM python:3.11.9-slim

# Install required system packages
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wakeonlan \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 -s /bin/bash ott && \
    mkdir -p /app /config && \
    chown -R ott:ott /app /config

# Set working directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY --chown=ott:ott requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=ott:ott ott/ ./ott/
COPY --chown=ott:ott main.py ./

# Switch to non-root user
USER ott

# Volume for configuration (allows external config mounting)
VOLUME ["/config"]

# Expose webhook port
EXPOSE 9123

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9123/health || exit 1

# Default command - new modular entry point
CMD ["python", "main.py", "run-all"]
