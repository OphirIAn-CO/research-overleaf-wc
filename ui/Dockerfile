# The web app's image: Python + git (pull/push) + poppler (book import) + the built-in browser (Chromium through Playwright).
# The app code is NOT baked in: ui.sh mounts the repo's scripts/ folder read-only at /app, so updating the skill never needs a rebuild
# (only this file changing does). The same image runs two containers: the web app, and the built-in browser on its own (see ui.sh), so
# that the pages of QuillBot and Overleaf never run next to the OpenAI key, the Overleaf token or the papers.
FROM python:3.12-slim-bookworm
# xvfb, xclip and chromium: the "real browser" mode (an ordinary Chromium on a virtual screen, used for QuillBot, whose human check turns away a scripted browser).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git poppler-utils ca-certificates xvfb xclip chromium fonts-liberation fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*
# LaTeX for the paper preview (the LaTeX pane compiles your paper here, nothing is sent anywhere): latexmk, the usual classes and packages (elsarticle, IEEE, Springer
# and other publisher classes, maths, tables, figures, biblatex with biber) and the standard fonts. About 1 GB. Build without it: OLS_WITH_LATEX=0 ./ui.sh build
ARG WITH_LATEX=1
RUN if [ "$WITH_LATEX" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends latexmk biber lmodern texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended \
         texlive-publishers texlive-science texlive-pictures texlive-bibtex-extra texlive-plain-generic \
      && rm -rf /var/lib/apt/lists/* ; \
    fi
LABEL ols.latex="$WITH_LATEX"
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir "playwright==1.63.0" "python-xlib==0.33" "pillow>=10" "websocket-client>=1.7" \
 && playwright install --with-deps chromium \
 && chmod -R a+rX /ms-playwright \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OVERLEAF_UI_IN_DOCKER=1
WORKDIR /app
EXPOSE 8765
CMD ["python3", "/app/ui/server.py"]
