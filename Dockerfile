FROM python:3.13.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8000

# Sin curl a propósito: la imagen slim no lo trae y python ya está dentro.
# /health devuelve 503 si un daemon thread (bot o scheduler) ha muerto, que es el fallo
# que dejaba el contenedor "Up" sin clasificar nada. start-period cubre el arranque:
# el startup de FastAPI espera 1 s antes de lanzar los threads.
HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"

CMD ["python", "-m", "gmail_inbox_bot", "--server"]
