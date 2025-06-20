import os
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from tasks import fact_check_video  # <-- Импортируем саму задачу
from celery.result import AsyncResult

# Flask автоматически найдет папки 'static' и 'templates'
app = Flask(__name__)
CORS(app)

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "URL is required"}), 400
    
    video_url = data['url']
    target_lang = data.get('lang', 'en') # Получаем язык из запроса

    # Запускаем задачу Celery асинхронно
    task = fact_check_video.delay(video_url, target_lang)
    
    # Возвращаем клиенту ID задачи
    return jsonify({"task_id": task.id}), 202

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    # Проверяем статус задачи по ее ID
    task_result = AsyncResult(task_id)
    
    if task_result.successful():
        result = task_result.get()
        return jsonify({
            "status": "SUCCESS",
            "result": result
        })
    elif task_result.failed():
        # Если внутри задачи произошла ошибка, возвращаем ее
        result = str(task_result.info) # task_result.info содержит исключение
        return jsonify({
            "status": "FAILURE",
            "result": result
        })
    else:
        # Задача еще выполняется
        return jsonify({"status": task_result.state})


# Этот маршрут будет обслуживать главную страницу
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    return render_template("index.html")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)