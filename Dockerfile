# Dockerfile (теперь расположен в propacondom-app/Dockerfile)

FROM python:3.12-slim

WORKDIR /app

# Копируем requirements.txt из backend/ в /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы бэкенда (app.py, tasks.py и т.д.) из backend/ в /app
COPY backend/. .

# Копируем папку frontend из корневой директории репозитория (propacondom-app/frontend/) в /frontend/ внутри контейнера.
COPY frontend/ /frontend/

ENV PORT 8080
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app