#!/bin/bash
# Mismo patrón que start-server.sh de 3darg-backend (Railway, Dockerfile +
# gunicorn). POSTGRES_HOST/POSTGRES_PORT son solo para el chequeo de espera
# de acá abajo; la conexión real de Django usa DATABASE_URL (ver
# config/settings.py, dj_database_url) que Railway inyecta por referencia de
# servicio (${{Postgres-rpE9.DATABASE_URL}}).

set -e

echo "--- 1. Esperando a que la base de datos esté lista ---"
DB_HOST="${POSTGRES_HOST:-db}"
DB_PORT="${POSTGRES_PORT:-5432}"
until (echo > "/dev/tcp/${DB_HOST}/${DB_PORT}") >/dev/null 2>&1; do
  echo "Postgres no responde todavía... esperando 1 segundo."
  sleep 1
done

echo "--- 2. Aplicando migraciones ---"
python manage.py migrate --noinput

echo "--- 3. Recolectando archivos estáticos ---"
python manage.py collectstatic --noinput

echo "--- 4. Iniciando servidor con gunicorn ---"
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-3}" \
  --timeout "${GUNICORN_TIMEOUT:-60}" \
  --access-logfile - \
  --error-logfile -
