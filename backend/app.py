import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from tasks import celery
from celery.result import AsyncResult

# Определяем абсолютный путь к папке frontend
# Это самый надежный способ, который будет работать всегда
frontend_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')

# --- ИЗМЕНЕНИЕ: Явно указываем Flask путь к статическим файлам ---
# static_url_path='' означает, что файлы будут доступны из корня (например, /style.css)
app = Flask(__name__, static_folder=frontend_folder, static_url_path='')
CORS(app)

# --- API-маршруты (Остаются без изменений) ---
@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Пожалуйста, укажите 'url' в теле запроса"}), 400
    lang = data.get('lang', 'en')
    task = celery.send_task('tasks.fact_check_video', args=[data['url'], lang])
    return jsonify({"task_id": task.id}), 202

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    task_result = AsyncResult(task_id, app=celery)
    if task_result.ready():
        return jsonify({"status": "SUCCESS", "result": task_result.result}) if task_result.successful() else jsonify({"status": "FAILURE", "result": str(task_result.info)})
    else:
        return jsonify({"status": "PENDING"}), 202

# --- Маршрут для раздачи главной страницы и статики ---
# Этот маршрут будет обрабатывать все остальные запросы
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(os.path.join(app.static_folder, path)):
        # Если запрашивается существующий файл (style.css, script.js), отдаем его
        return send_from_directory(app.static_folder, path)
    else:
        # В любом другом случае (например, при обновлении страницы /report/xyz)
        # отдаем главный index.html, чтобы JavaScript мог сам разобраться с маршрутом
        return send_from_directory(app.static_folder, 'index.html')