FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Default start (Render, no Docker Command override): apply pending migrations, then serve. If a migration fails the container exits, the new deploy
# fails its health check, and Render keeps serving the previous version. EVENT_WORKER_IN_PROCESS defaults to true here because the free plan has no
# separate worker service (it can be overridden by the environment). docker-compose sets its own command per service, so this default does not touch it.
CMD ["sh", "-c", "export EVENT_WORKER_IN_PROCESS=\"${EVENT_WORKER_IN_PROCESS:-true}\" && alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
