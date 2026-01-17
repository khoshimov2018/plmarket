# Polymarket Esports Arbitrage Bot
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for SQLite
RUN mkdir -p /app/data

# Environment variables (override in deployment)
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "print('healthy')" || exit 1

# Run the bot - use --live flag when PAPER_TRADING=false
# The entrypoint script will check PAPER_TRADING env var
# --yes skips the confirmation prompt for automated deployments
CMD ["sh", "-c", "if [ \"$PAPER_TRADING\" = \"false\" ]; then python main.py run --live --yes; else python main.py run --paper; fi"]
