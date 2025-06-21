import os
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from celery.result import AsyncResult

# --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
# Импортируем 'celery' из tasks.py и переименовываем его в 'celery_app'
# Также импортируем 'db'
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

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {
        'status': task_result.state,
        'info': task_result.info if task_result.state != 'SUCCESS' else None,
        'result': task_result.result if task_result.state == 'SUCCESS' else None
    }
    return jsonify(response_data)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    if path.startswith('report/'):
        analysis_id = path.split('/')[1]
        try:
            doc_ref = db.collection('analyses').document(analysis_id)
            doc = doc_ref.get()
            if doc.exists:
                return render_template('report.html', report=doc.to_dict())
            else:
                return "Report not found", 404
        except Exception as e:
            print(f"Error fetching report {analysis_id}: {e}") 
            return f"An error occurred: {e}", 500
    
    return render_template("index.html")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)