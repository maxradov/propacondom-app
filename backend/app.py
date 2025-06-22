import os
from flask import Flask, request, jsonify, render_template
from google.cloud.firestore_v1.query import Query

from flask_cors import CORS
from celery.result import AsyncResult
# --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
# Импортируем 'datetime' для проверки типа
from datetime import datetime
# Импортируем правильные объекты из tasks.py
from tasks import celery as celery_app, db

app = Flask(__name__)
CORS(app)

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "URL is required"}), 400
    
    video_url = data['url']
    target_lang = data.get('lang', 'en')
    
    task = celery_app.send_task('tasks.fact_check_video', args=[video_url, target_lang])
    return jsonify({"task_id": task.id}), 202

# Замените этот эндпоинт в вашем файле app.py

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    try:
        task_result = AsyncResult(task_id, app=celery_app)
        
        # ИЗМЕНЕНИЕ: Обернули логику в try...except
        response_data = {
            'status': task_result.state,
            'info': task_result.info if task_result.state != 'SUCCESS' else None,
            'result': task_result.result if task_result.state == 'SUCCESS' else None
        }
        return jsonify(response_data)
    except Exception as e:
        # Если что-то пошло не так (например, нет связи с Redis),
        # возвращаем ошибку, а не падаем
        print(f"Error getting task status for {task_id}: {e}")
        return jsonify({'status': 'FAILURE', 'result': 'Could not retrieve task status from backend.'}), 500

def get_analyses(last_timestamp_str=None):
    """Вспомогательная функция для получения порции анализов из Firestore."""
    query = db.collection('analyses').order_by('created_at', direction=Query.DESCENDING)
    
    # Если передан timestamp последнего видео, начинаем поиск после него (пагинация)
    if last_timestamp_str:
        # Firestore требует объект datetime для курсора, конвертируем строку обратно
        last_timestamp = datetime.datetime.fromisoformat(last_timestamp_str)
        query = query.start_after({'created_at': last_timestamp})
    
    query = query.limit(5)
    
    results = []
    for doc in query.stream():
        data = doc.to_dict()
        data['id'] = doc.id
        # Убедимся, что дата всегда в строковом формате для JSON
        if 'created_at' in data and hasattr(data['created_at'], 'isoformat'):
            data['created_at'] = data['created_at'].isoformat()
        results.append(data)
    return results

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    # Этот блок остается без изменений. Он обрабатывает страницы отчетов.
    if path.startswith('report/'):
        analysis_id = path.split('/')[1]
        try:
            doc_ref = db.collection('analyses').document(analysis_id)
            doc = doc_ref.get()
            if doc.exists:
                report_data = doc.to_dict()
                # Проверяем, есть ли поле 'created_at' и является ли оно объектом datetime
                if 'created_at' in report_data and isinstance(report_data['created_at'], datetime):
                    # Преобразуем его в строку формата ISO, понятную для JSON
                    report_data['created_at'] = report_data['created_at'].isoformat()
                return render_template('report.html', report=report_data)
            else:
                return "Report not found", 404
        except Exception as e:
            print(f"Error fetching report {analysis_id}: {e}")
            return f"An error occurred: {e}", 500

    # --- СЮДА ВСТАВЛЯЕТСЯ ВАШ ВТОРОЙ СКРИНШОТ ---
    # Этот блок проверяет, является ли страница главной (т.е. path пустой).
    if not path:
        try:
            # Загружаем первую порцию из 5 видео
            recent_analyses = get_analyses()
            return render_template("index.html", recent_analyses=recent_analyses)
        except Exception as e:
            print(f"Error fetching initial analyses: {e}")
            return render_template("index.html", recent_analyses=[])
    # --- КОНЕЦ ВСТАВКИ ---

    # Если путь не является ни отчетом, ни главной страницей,
    # можно вернуть либо страницу 404, либо главную без данных.
    return render_template("index.html", recent_analyses=[])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)