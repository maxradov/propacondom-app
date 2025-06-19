import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from tasks import celery
from celery.result import AsyncResult

# Определяем путь к папке frontend внутри контейнера
# Dockerfile копирует папку 'frontend' в корень контейнера (/frontend)
frontend_folder = '/frontend'

# Flask будет обслуживать статические файлы из frontend_folder
# static_url_path='/static' означает, что style.css будет доступен по /static/style.css
app = Flask(__name__, static_folder=frontend_folder, static_url_path='/static')
CORS(app)

# --- API-маршруты ---
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

# --- Маршрут для раздачи главной страницы (index.html) ---
@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

# --- Маршрут для раздачи других SPA-маршрутов (которые обрабатывает JS) ---
@app.route('/<path:path>')
def serve_spa_paths(path):
    return send_from_directory(app.static_folder, 'index.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"Starting Flask app on 0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port)