import os
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
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
    task_result = AsyncResult(task_id, app=celery_app)
    
    if task_result.state == 'SUCCESS':
        return jsonify({'status': 'SUCCESS', 'result': task_result.get()})
    
    elif task_result.state == 'FAILURE':
        # Создаем более надежный ответ об ошибке
        response = {
            'status': 'FAILURE',
            'result': str(task_result.info) # .info содержит исключение
        }
        return jsonify(response), 500 # Возвращаем 500, чтобы фронтенд понял, что это ошибка
        
    else:
        # Эта ветка теперь обрабатывает и PENDING, и наш новый PROGRESS
        return jsonify({'status': task_result.state, 'info': task_result.info or {}})


# Этот маршрут будет обслуживать главную страницу
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    return render_template("index.html")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)