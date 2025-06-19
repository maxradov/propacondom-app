# Dockerfile (расположен в propacondom-app/backend/)

# Используем официальный образ Python
FROM python:3.12-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл с зависимостями, находящийся в текущей директории (backend/)
COPY requirements.txt requirements.txt
# Устанавливаем зависимости (убедитесь, что gunicorn тоже в requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы бэкенда из текущей директории (backend/)
COPY . .

# *** ВАЖНО: ЭТУ СТРОКУ УДАЛИТЬ ИЗ Dockerfile, ТАК КАК frontend НАХОДИТСЯ ЗА ПРЕДЕЛАМИ КОНТЕКСТА СБОРКИ
# COPY frontend/ /frontend/ 
# Мы скопируем frontend с помощью отдельного шага в cloudbuild.yaml

# Указываем, как запускать приложение с помощью gunicorn
# Он будет слушать порт, который ему предоставит Google Cloud (переменная окружения $PORT)
# 'app:app' означает, что gunicorn будет искать приложение 'app' в файле 'app.py'
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app