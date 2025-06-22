import os
from flask import Flask, request, jsonify, render_template, g, make_response, redirect, url_for
from flask_cors import CORS
from celery.result import AsyncResult
from datetime import datetime
from google.cloud.firestore_v1.query import Query
from flask_babel import Babel, _

# Импортируем объекты из tasks.py
from tasks import celery as celery_app, db

# --- App Configuration ---
app = Flask(__name__)
CORS(app)

# --- Babel (i18n) Configuration ---
LANGUAGES = {
    'en': {'name': 'English', 'flag': '🇺🇸'},
    'es': {'name': 'Español', 'flag': '🇪🇸'},
    'zh': {'name': '中文', 'flag': '🇨🇳'},
    'hi': {'name': 'हिन्दी', 'flag': '🇮🇳'},
    'fr': {'name': 'Français', 'flag': '🇫🇷'},
    'ar': {'name': 'العربية', 'flag': '🇸🇦'},
    'bn': {'name': 'বাংলা', 'flag': '🇧🇩'},
    'ru': {'name': 'Русский', 'flag': '🇷🇺'},
    'pt': {'name': 'Português', 'flag': '🇵🇹'},
    'de': {'name': 'Deutsch', 'flag': '🇩🇪'}
}
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
babel = Babel(app)

@babel.localeselector
def get_locale():
    # 1. Check for language in cookie
    lang_code = request.cookies.get('lang')
    if lang_code in LANGUAGES:
        return lang_code
    # 2. Fallback to browser's Accept-Languages header
    return request.accept_languages.best_match(list(LANGUAGES.keys()))

@app.context_processor
def inject_conf_var():
    # Pass language info to all templates
    return dict(
        LANGUAGES=LANGUAGES,
        CURRENT_LANG=get_locale()
    )

@app.route('/set_language/<lang>')
def set_language(lang):
    if lang not in LANGUAGES:
        lang = app.config['BABEL_DEFAULT_LOCALE']
    
    # Redirect to the same page the user was on
    response = make_response(redirect(request.referrer or url_for('serve_index')))
    response.set_cookie('lang', lang, max_age=60*60*24*365*2) # Set for 2 years
    return response

# --- API Эндпоинты (без изменений) ---

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "URL is required"}), 400
    
    video_url = data['url']
    target_lang = data.get('lang', get_locale())
    if target_lang not in LANGUAGES:
        target_lang = app.config['BABEL_DEFAULT_LOCALE']
        
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
    query = db.collection('analyses').order_by('created_at', direction=Query.DESCENDING)
    
    if last_timestamp_str and isinstance(last_timestamp_str, str) and last_timestamp_str.strip():
        try:
            last_timestamp = datetime.fromisoformat(last_timestamp_str)
            query = query.start_after({'created_at': last_timestamp})
        except ValueError:
            print(f"Warning: Could not parse timestamp: '{last_timestamp_str}'")
            return []
    
    query = query.limit(10)
    
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

# --- Эндпоинты для отображения страниц (без изменений) ---

@app.route('/', methods=['GET'])
def serve_index():
    try:
        recent_analyses = get_analyses()
        url_param = request.args.get('url', '')
        return render_template("index.html", recent_analyses=recent_analyses, initial_url=url_param)
    except Exception as e:
        print(f"Error fetching initial analyses: {e}")
        return render_template("index.html", recent_analyses=[], initial_url='')

@app.route('/report/<analysis_id>', methods=['GET'])
def serve_report(analysis_id):
    try:
        doc_ref = db.collection('analyses').document(analysis_id)
        doc = doc_ref.get()
        if doc.exists:
            report_data = doc.to_dict()
            if 'created_at' in report_data and hasattr(report_data['created_at'], 'isoformat'):
                report_data['created_at'] = report_data['created_at'].isoformat()
            return render_template('report.html', report=report_data)
        else:
            return _("Report not found"), 404
    except Exception as e:
        print(f"Error fetching report {analysis_id}: {e}")
        return f"{_('An error occurred')}: {e}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)