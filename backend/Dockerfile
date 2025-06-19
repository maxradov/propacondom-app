# Dockerfile (расположен в propacondom-app/backend/)

# Используем официальный образ Python
FROM python:3.12-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt requirements.txt

# Устанавливаем зависимости (убедитесь, что gunicorn тоже в requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы бэкенда (app.py, tasks.py и т.д.)
# Они будут находиться в /app внутри контейнера
COPY . .

# *** ВАЖНОЕ ИЗМЕНЕНИЕ: Копируем папку frontend из родительской директории ***
# Эта команда возьмет содержимое папки 'frontend' из корневого каталога вашего проекта
# (propacondom-app/frontend/) и скопирует его в /frontend/ внутри контейнера.
# Это позволит app.py найти папку 'frontend' по пути '../frontend' относительно /app.
COPY ../frontend/ /frontend/

# Указываем, как запускать приложение с помощью gunicorn
# Он будет слушать порт, который ему предоставит Google Cloud (переменная окружения $PORT)
# 'app:app' означает, что gunicorn будет искать приложение 'app' в файле 'app.py'
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app