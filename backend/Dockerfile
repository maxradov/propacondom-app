# Dockerfile (расположен в propacondom-app/backend/)

# Используем официальный образ Python
FROM python:3.12-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# *** ИЗМЕНЕНО: Путь к requirements.txt относительно корня репозитория ***
# Теперь Docker Build видит requirements.txt как backend/requirements.txt
COPY backend/requirements.txt . 
# Устанавливаем зависимости (убедитесь, что gunicorn тоже в requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# *** ИЗМЕНЕНО: Путь к файлам бэкенда относительно корня репозитория ***
# Копируем app.py, tasks.py и т.д. из backend/ в /app
COPY backend/. . 

# *** ЭТА СТРОКА ОСТАЕТСЯ БЕЗ ИЗМЕНЕНИЙ, так как frontend/ уже в корне контекста сборки ***
COPY frontend/ /frontend/

# Указываем, как запускать приложение с помощью gunicorn
# Он будет слушать порт, который ему предоставит Google Cloud (переменная окружения $PORT)
# 'app:app' означает, что gunicorn будет искать приложение 'app' в файле 'app.py'
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app