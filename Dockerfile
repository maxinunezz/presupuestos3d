FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m appuser

# /app es root:root (lo crea WORKDIR antes del useradd). A diferencia de
# 3darg-backend, acá `staticfiles/` y `media/` están en .gitignore (no
# vienen en el COPY de abajo), así que collectstatic/uploads necesitan
# poder crear esas carpetas en runtime como appuser — sin este chown,
# Django tira PermissionError al hacer os.makedirs("/app/staticfiles").
RUN mkdir -p /app/staticfiles /app/media && chown -R appuser:appuser /app

COPY --chown=appuser:appuser . .

USER appuser

CMD ["./start-server.sh"]
