# Используем базовый образ Python 3.12
FROM python:3.12-slim

# Устанавливаем системные зависимости, включая Supervisor
RUN apt-get update && apt-get install -y supervisor

# Создаем и активируем виртуальное окружение
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем requirements.txt и устанавливаем зависимости Python ВНУТРЬ venv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем файл конфигурации Supervisor
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Копируем код нашего приложения
COPY backend/. .

# Указываем порт
EXPOSE 8080

# Финальная команда: запускаем Supervisor, который запустит Gunicorn и Celery
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]