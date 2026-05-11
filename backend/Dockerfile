FROM python:3.12-slim

WORKDIR /app

# Playwright Chromium needs system libs. We install Chromium via the
# Playwright CLI (one liner below), but it still needs base shared
# libraries to start headless. Bundle is ~250MB but only built once.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        ca-certificates \
        fonts-liberation \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Install Chromium for Playwright. We only need chromium — saves
# ~400MB vs the full bundle (firefox + webkit).
RUN python -m playwright install chromium

# Persistent dir for screenshots — bind-mounted at runtime so they
# survive container rebuilds.
RUN mkdir -p /app/screenshots

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
