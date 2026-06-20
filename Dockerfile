# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Git is needed by hatchling when building from a git checkout.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

# Install the package with runtime dependencies only (no dev extras).
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src:$PYTHONPATH

CMD ["factorydaemon", "telegram"]
