FROM python:3.12-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем только requirements.txt для кэширования слоев Docker
COPY backend/requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем всё остальное из папки backend (включая static и templates)
COPY backend/. .

# Указываем порт
ENV PORT 8080
EXPOSE 8080

# Запускаем приложение через Gunicorn
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app