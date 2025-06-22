import os
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from celery.result import AsyncResult
from datetime import datetime
from google.cloud.firestore_v1.query import Query

# Импортируем объекты из tasks.py
from tasks import celery_app, db

app = Flask(__name__)
CORS(app)

# --- API Эндпоинты ---

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "URL is required"}), 400
    
    video_url = data['url']
    target_lang = data.get('lang', 'en')
    task = celery_app.send_task('tasks.fact_check_video', args=[video_url, target_lang])
    return jsonify({"task_id": task.id}), 202

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    try:
        task_result = AsyncResult(task_id, app=celery_app)
        response_data = {
            'status': task_result.state,
            'info': task_result.info if task_result.state != 'SUCCESS' else None,
            'result': task_result.result if task_result.state == 'SUCCESS' else None
        }
        return jsonify(response_data)
    except Exception as e:
        print(f"Error getting task status for {task_id}: {e}")
        return jsonify({'status': 'FAILURE', 'result': 'Could not retrieve task status from backend.'}), 500

def get_analyses(last_timestamp_str=None):
    """Вспомогательная функция для получения порции анализов из Firestore."""
    query = db.collection('analyses').order_by('created_at', direction=Query.DESCENDING)
    
    if last_timestamp_str and isinstance(last_timestamp_str, str) and last_timestamp_str.strip():
        try:
            last_timestamp = datetime.fromisoformat(last_timestamp_str)
            query = query.start_after({'created_at': last_timestamp})
        except ValueError:
            print(f"Warning: Could not parse timestamp: '{last_timestamp_str}'")
            return []
    
    query = query.limit(5)
    results = []
    for doc in query.stream():
        data = doc.to_dict()
        data['id'] = doc.id
        if 'created_at' in data and hasattr(data['created_at'], 'isoformat'):
            data['created_at'] = data['created_at'].isoformat()
        results.append(data)
    return results

@app.route('/api/get_recent_analyses', methods=['GET'])
def api_get_recent_analyses():
    last_timestamp = request.args.get('last_timestamp')
    try:
        analyses = get_analyses(last_timestamp)
        return jsonify(analyses)
    except Exception as e:
        print(f"Error in /api/get_recent_analyses: {e}")
        return jsonify({"error": "Failed to fetch more analyses"}), 500

# --- Эндпоинты для отображения страниц ---

@app.route('/', methods=['GET'])
def serve_index():
    """Отдельная функция для главной страницы."""
    try:
        recent_analyses = get_analyses()
        url_param = request.args.get('url', '')
        return render_template("index.html", recent_analyses=recent_analyses, initial_url=url_param)
    except Exception as e:
        print(f"Error fetching initial analyses: {e}")
        return render_template("index.html", recent_analyses=[], initial_url='')

@app.route('/report/<analysis_id>', methods=['GET'])
def serve_report(analysis_id):
    """Отдельная функция для страниц отчетов."""
    try:
        doc_ref = db.collection('analyses').document(analysis_id)
        doc = doc_ref.get()
        if doc.exists:
            report_data = doc.to_dict()
            if 'created_at' in report_data and hasattr(report_data['created_at'], 'isoformat'):
                report_data['created_at'] = report_data['created_at'].isoformat()
            return render_template('report.html', report=report_data)
        else:
            return "Report not found", 404
    except Exception as e:
        print(f"Error fetching report {analysis_id}: {e}")
        return f"An error occurred: {e}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)