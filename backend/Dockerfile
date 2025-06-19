# Используем официальный образ Python
FROM python:3.12-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt requirements.txt

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы бэкенда
COPY . .

# Указываем, как запускать приложение с помощью gunicorn
# Он будет слушать порт, который ему предоставит Google Cloud
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app