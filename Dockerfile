# Используем базовый образ Python
FROM python:3.12-slim

# Устанавливаем системные зависимости (Supervisor нам пока не нужен)
# RUN apt-get update && apt-get install -y supervisor

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем requirements.txt и устанавливаем зависимости Python
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код нашего приложения
COPY backend/. .

# Указываем порт
EXPOSE 8080

# ↓↓↓ ВРЕМЕННОЕ ИЗМЕНЕНИЕ ДЛЯ ОТЛАДКИ ↓↓↓
# Запускаем не Supervisor, а напрямую Celery-воркер.
# Это позволит нам увидеть его стартовую ошибку.
CMD ["celery", "-A", "tasks.celery", "worker", "--loglevel=info"]