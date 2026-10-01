# The web app's container: Python + git (pull/push) + poppler (book import). The app code is NOT baked in: ui.sh mounts the
# repo's scripts/ folder read-only at /app, so updating the skill never needs a rebuild.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git poppler-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OVERLEAF_UI_IN_DOCKER=1
WORKDIR /app
EXPOSE 8765
CMD ["python3", "/app/ui/server.py"]
