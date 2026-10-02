# The web app's image: Python + git (pull/push) + poppler (book import) + the built-in browser (Chromium through Playwright).
# The app code is NOT baked in: ui.sh mounts the repo's scripts/ folder read-only at /app, so updating the skill never needs a rebuild
# (only this file changing does). The same image runs two containers: the web app, and the built-in browser on its own (see ui.sh), so
# that the pages of QuillBot and Overleaf never run next to the OpenAI key, the Overleaf token or the papers.
FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends git poppler-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir "playwright==1.63.0" \
 && playwright install --with-deps chromium \
 && chmod -R a+rX /ms-playwright \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OVERLEAF_UI_IN_DOCKER=1
WORKDIR /app
EXPOSE 8765
CMD ["python3", "/app/ui/server.py"]
