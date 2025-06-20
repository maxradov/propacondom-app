import os
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
# ↓↓↓ ИЗМЕНЕНИЕ: Импортируем сам объект celery, а не отдельную задачу
from tasks import celery as celery_app
from celery.result import AsyncResult

app = Flask(__name__)
CORS(app)

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "URL is required"}), 400
    
    video_url = data['url']
    target_lang = data.get('lang', 'en')

    # Здесь мы используем celery_app.send_task, чтобы запустить задачу по имени
    # Это более надежный способ, чем импорт самой функции задачи
    task = celery_app.send_task('tasks.fact_check_video', args=[video_url, target_lang])
    
    return jsonify({"task_id": task.id}), 202

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    # ↓↓↓ ИЗМЕНЕНИЕ: Передаем наш настроенный celery_app в AsyncResult
    task_result = AsyncResult(task_id, app=celery_app)
    
    if task_result.successful():
        result = task_result.get()
        return jsonify({
            "status": "SUCCESS",
            "result": result
        })
    elif task_result.failed():
        result = str(task_result.info)
        return jsonify({
            "status": "FAILURE",
            "result": result
        })
    else:
        # Задача еще выполняется (PENDING, STARTED, RETRY)
        return jsonify({"status": task_result.state})

# Этот маршрут будет обслуживать главную страницу
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    return render_template("index.html")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)