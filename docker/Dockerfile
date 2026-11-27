# Image Aiffel : application FastAPI (et, plus tard, le worker STT avec la même image).
FROM python:3.12-slim AS build
# git : seulement pour les extensions tirées d'un dépôt (AIFFEL_EXTENSIONS), dans l'étape de build.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.11
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra stt --no-install-project
COPY aiffel ./aiffel
COPY runtime ./runtime
RUN uv sync --frozen --no-dev --extra stt
# Extensions : paquets Python qui se déclarent par l'entry point « aiffel.extensions ».
# 1. Celles présentes dans extensions/ (développement local) ; absentes : rien à installer.
COPY extensions ./extensions
RUN for ext in extensions/*/; do \
      if [ -f "${ext}pyproject.toml" ]; then uv pip install --python .venv/bin/python "$ext"; fi; \
    done
# 2. Celles tirées d'un dépôt git, éventuellement privé : AIFFEL_EXTENSIONS liste des URL pip
#    séparées par des espaces. Image d'Aiffel en ligne :
#      git+https://github.com/ekylibre/ekylibre-billing.git@main#subdirectory=client
#    Le jeton GitHub est un secret de build (« github_token ») : il ne sert qu'à cette commande, par
#    l'environnement de git, et n'est écrit dans aucune couche. Vide (image publique) : rien.
ARG AIFFEL_EXTENSIONS=""
RUN --mount=type=secret,id=github_token \
    if [ -n "$AIFFEL_EXTENSIONS" ]; then \
      if [ -s /run/secrets/github_token ]; then \
        export GIT_CONFIG_COUNT=1 \
          GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat /run/secrets/github_token)@github.com/.insteadOf" \
          GIT_CONFIG_VALUE_0="https://github.com/"; \
      fi; \
      uv pip install --python .venv/bin/python $AIFFEL_EXTENSIONS; \
    fi

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home /app aiffel
WORKDIR /app
COPY --from=build --chown=aiffel:aiffel /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER aiffel
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "aiffel.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
