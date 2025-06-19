# Dockerfile (расположен в propacondom-app/backend/)

# Используем официальный образ Python
FROM python:3.12-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл с зависимостями
# Контекст сборки - ./backend, поэтому requirements.txt находится прямо там.
COPY requirements.txt requirements.txt

# Устанавливаем зависимости (убедитесь, что gunicorn тоже в requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы бэкенда
# Контекст сборки - ./backend, поэтому app.py, tasks.py и т.д. находятся прямо там.
COPY . .

# *** ВАЖНОЕ ИЗМЕНЕНИЕ ***:
# Мы хотим скопировать папку frontend, которая находится НА УРОВЕНЬ ВЫШЕ контекста сборки (../frontend).
# Для этого нужно, чтобы Cloud Build *не ограничивал* контекст сборки только backend.
# НО! Ваш cloudbuild.yaml УЖЕ ограничивает контекст до './backend'.
# Поэтому эту строку в Dockerfile нужно изменить на копирование ИЗНУТРИ контекста.

# Если вы хотите сохранить Cloud Build с контекстом `./backend`,
# то скопировать `../frontend` напрямую из Dockerfile невозможно.
# Вам нужно будет изменить стратегию:
# 1. Либо изменить cloudbuild.yaml, чтобы контекст был `.` (корень репозитория).
#    (Это то, что я пытался сделать ранее, но это вызывало другие ошибки из-за путей внутри Dockerfile).
# 2. Либо добавить отдельный шаг в cloudbuild.yaml для копирования frontend.

# Учитывая, что вы хотите краткое решение и уже настроили Cloud Build на './backend',
# давайте скопируем `frontend` через команду в cloudbuild.yaml, а не через Dockerfile.
# Удалите эту строку из Dockerfile!

# COPY frontend/ /frontend/  <--- УДАЛИТЕ ЭТУ СТРОКУ ИЗ Dockerfile!

# Указываем, как запускать приложение с помощью gunicorn
# Он будет слушать порт, который ему предоставит Google Cloud (переменная окружения $PORT)
# 'app:app' означает, что gunicorn будет искать приложение 'app' в файле 'app.py'
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app