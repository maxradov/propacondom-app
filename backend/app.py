import os
from flask import Flask, request, jsonify, render_template, make_response, redirect, url_for
from flask_cors import CORS
from celery.result import AsyncResult
from datetime import datetime
from google.cloud.firestore_v1.query import Query
from flask_babel import Babel, _
from datetime import datetime, timezone, timedelta
from constants import CACHE_EXPIRATION_DAYS

from celery_init import celery as celery_app
from tasks import get_db_client

app = Flask(__name__)
CORS(app)

@app.before_request
def redirect_to_new_domain():
    old_domain = "propacondom.com"
    new_domain = "factchecking.pro"
    if request.host.startswith(old_domain):
        new_url = f"https://{new_domain}{request.full_path}"
        if new_url.endswith('?'):
            new_url = new_url[:-1]
        return redirect(new_url, code=301)

class FlaskTask(celery_app.Task):
    def __call__(self, *args, **kwargs):
        with app.app_context():
            return self.run(*args, **kwargs)

celery_app.Task = FlaskTask

LANGUAGES = {
    'en': {'name': 'English', 'flag': '🇺🇸'},
    'es': {'name': 'Español', 'flag': '🇪🇸'},
    'zh': {'name': '中文', 'flag': '🇨🇳'},
    'hi': {'name': 'हिन्दी', 'flag': '🇮🇳'},
    'fr': {'name': 'Français', 'flag': '🇫🇷'},
    'ar': {'name': 'العربية', 'flag': '🇸🇦'},
    'bn': {'name': 'বাংলা', 'flag': '🇧🇩'},
    'ru': {'name': 'Русский', 'flag': '🇷🇺'},
    'uk': {'name': 'Українська', 'flag': '🇺🇦'},
    'pt': {'name': 'Português', 'flag': '🇵🇹'},
    'de': {'name': 'Deutsch', 'flag': '🇩🇪'}
}
app.config['BABEL_DEFAULT_LOCALE'] = 'en'

def get_locale():
    # Priority 1: lang_code from URL view arguments. This is the definitive source for a request's language.
    if request.view_args and 'lang_code' in request.view_args:
        url_lang_code = request.view_args.get('lang_code')
        if url_lang_code in LANGUAGES:
            return url_lang_code

    # Fallback for contexts where URL lang_code isn't available (e.g. initial '/' redirect logic)
    cookie_lang_code = request.cookies.get('lang')
    if cookie_lang_code in LANGUAGES:
        return cookie_lang_code

    best_match = request.accept_languages.best_match(list(LANGUAGES.keys()))
    if best_match:
        return best_match

    return app.config['BABEL_DEFAULT_LOCALE']

babel = Babel(app, locale_selector=get_locale)

@app.context_processor
def inject_conf_var():
    # get_locale() now correctly prioritizes lang_code from URL view_args if present.
    # So, CURRENT_LANG for templates will reflect the language determined by the URL on prefixed routes.
    current_lang_in_context = get_locale()

    return dict(
        LANGUAGES=LANGUAGES,
        CURRENT_LANG=current_lang_in_context
    )

@app.route('/api/report/<analysis_id>')
def get_report_or_selection(analysis_id):
    db = get_db_client()
    doc = db.collection('analyses').document(analysis_id).get()
    if not doc.exists:
        return jsonify({'error': 'Not found'}), 404
    data = doc.to_dict()
    status = data.get('status', 'UNKNOWN')
    if status == 'COMPLETED':
        data['id'] = analysis_id
        # Явно добавляем extracted_claims в выдачу (если вдруг отсутствует)
        if 'extracted_claims' not in data:
            # Подгрузи claims из анализа (обычно он там всегда есть)
            db = get_db_client()
            doc = db.collection('analyses').document(analysis_id).get()
            if doc.exists:
                doc_data = doc.to_dict()
                data['extracted_claims'] = doc_data.get('extracted_claims', [])
            else:
                data['extracted_claims'] = []
        return jsonify(data)
    elif status == 'PENDING_SELECTION':
        # Только клеймы и метаданные — для показа выбора на странице репорта
        claims_ref = db.collection('claims')
        cache_expiry_date = datetime.now(timezone.utc) - timedelta(days=CACHE_EXPIRATION_DAYS)
        claims_for_selection = []
        for claim in data.get("extracted_claims", []):
            claim_hash = claim["hash"]
            claim_text = claim["text"]
            claim_doc = claims_ref.document(claim_hash).get()
            claim_info = {
                "hash": claim_hash,
                "text": claim_text
            }
            if claim_doc.exists:
                cached_data = claim_doc.to_dict()
                last_checked = cached_data.get('last_checked_at')
                if last_checked and last_checked.replace(tzinfo=timezone.utc) > cache_expiry_date:
                    claim_info["is_cached"] = True
                    claim_info["cached_data"] = {
                        "verdict": cached_data.get("verdict", ""),
                        "last_checked_at": str(last_checked)
                    }
                else:
                    claim_info["is_cached"] = False
            else:
                claim_info["is_cached"] = False
            claims_for_selection.append(claim_info)
        return jsonify({
            "status": "PENDING_SELECTION",
            "claims_for_selection": claims_for_selection,
            "video_title": data.get("video_title") or data.get("title") or "",
            "thumbnail_url": data.get("thumbnail_url", ""),
            "source_url": data.get("source_url", ""),
            "id": analysis_id,
            "input_type": data.get("input_type", "youtube")
        })
    else:
        return jsonify({'error': 'Analysis not complete.'}), 400


@app.route('/api/fact_check_selected', methods=['POST'])
def fact_check_selected():
    data = request.get_json()
    # ИСПРАВЛЕНО: Проверяем и используем ключ 'selected_claims_data'
    if not data or 'analysis_id' not in data or 'selected_claims_data' not in data:
        return jsonify({"error": "analysis_id and a list of selected_claims_data are required"}), 400

    analysis_id = data['analysis_id']
    selected_claims = data['selected_claims_data'] # ИСПРАВЛЕНО

    # Start the *fact-checking* task
    task = celery_app.send_task('tasks.fact_check_selected', args=[analysis_id, selected_claims])
    return jsonify({"task_id": task.id}), 202

@app.route('/set_language/<target_lang_code>')
def set_language(target_lang_code):
    if target_lang_code not in LANGUAGES:
        target_lang_code = app.config['BABEL_DEFAULT_LOCALE']

    redirect_url = url_for('serve_index', lang_code=target_lang_code) # Default redirect

    if request.referrer:
        referrer_path = request.referrer.split(request.host_url, 1)[-1] # Get path part of referrer

        # Try to match referrer path to known patterns and replace lang_code
        # This is a simplified approach. A more robust solution would use Flask's routing
        # to parse the referrer and rebuild the URL, or pass redirect info via query params.
        path_parts = referrer_path.strip('/').split('/')

        if len(path_parts) > 0 and path_parts[0] in LANGUAGES:
            # Referrer seems to be a prefixed URL, e.g., /de/report/xyz or /es/
            old_lang_code = path_parts[0]

            # Reconstruct the endpoint and args if possible (this part is tricky without more context)
            # For example, if it's /<old_lang_code>/report/<id>
            if len(path_parts) > 2 and path_parts[1] == 'report':
                analysis_id = path_parts[2]
                try:
                    redirect_url = url_for('serve_report', lang_code=target_lang_code, analysis_id=analysis_id)
                except Exception: # Werkzeug BuildError if endpoint not found or args mismatch
                    pass # Stick to default homepage redirect
            elif len(path_parts) == 1: # Homepage like /de/
                 redirect_url = url_for('serve_index', lang_code=target_lang_code)
            # Add more rules here if other prefixed URL patterns exist

    response = make_response(redirect(redirect_url))
    response.set_cookie('lang', target_lang_code, max_age=60*60*24*365*2)
    return response

# Keep API routes and other specific routes like /set_language defined before generic content routes.

@app.route('/', methods=['GET'])
def root_redirect():
    # Determine preferred language: 1. Cookie, 2. Browser, 3. Default ('en')
    determined_lang_code = app.config['BABEL_DEFAULT_LOCALE'] # Default to 'en'

    cookie_lang = request.cookies.get('lang')
    if cookie_lang and cookie_lang in LANGUAGES:
        determined_lang_code = cookie_lang
    else:
        browser_lang = request.accept_languages.best_match(list(LANGUAGES.keys()))
        if browser_lang:
            determined_lang_code = browser_lang

    return redirect(url_for('serve_index', lang_code=determined_lang_code))

# API routes should be defined before the generic <lang_code> routes
# Ensure all API routes are above the content routes that will include <lang_code>

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Input is required"}), 400
    user_input = data['url']
    # For API calls, language context for 'tasks.extract_claims' might need to be handled differently
    # or passed explicitly in the API request if it influences backend processing.
    # For now, using get_locale which will check cookie/browser if not a view_args context.
    target_lang = data.get('lang', get_locale())
    if target_lang not in LANGUAGES:
        target_lang = app.config['BABEL_DEFAULT_LOCALE']
    task = celery_app.send_task('tasks.extract_claims', args=[user_input, target_lang])
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
    db = get_db_client()
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

# Content Routes with Language Prefix
@app.route('/<lang_code>/', methods=['GET'])
def serve_index(lang_code):
    if lang_code not in LANGUAGES:
        # Optionally, redirect to a default language version or show 404
        # For now, get_locale will handle falling back for Babel, but URL is still invalid.
        # Redirecting to 'en' version of homepage:
        return redirect(url_for('serve_index', lang_code=app.config['BABEL_DEFAULT_LOCALE']))
    try:
        recent_analyses = get_analyses()
        url_param = request.args.get('url', '')
        return render_template("index.html", recent_analyses=recent_analyses, initial_url=url_param)
    except Exception as e:
        print(f"Error fetching initial analyses for lang {lang_code}: {e}")
        # Render with default lang if error, or handle error more gracefully
        return render_template("index.html", recent_analyses=[], initial_url='')

# Redirect for old non-prefixed report URLs
@app.route('/report/<analysis_id>', methods=['GET'])
def redirect_old_report(analysis_id):
    # Determine a language for the redirect, e.g., 'en' or from cookie/browser
    # For simplicity, redirecting to 'en' version. Could be enhanced.
    default_lang_for_redirect = app.config['BABEL_DEFAULT_LOCALE']
    cookie_lang = request.cookies.get('lang')
    if cookie_lang and cookie_lang in LANGUAGES:
        default_lang_for_redirect = cookie_lang
    else:
        browser_lang = request.accept_languages.best_match(list(LANGUAGES.keys()))
        if browser_lang:
            default_lang_for_redirect = browser_lang

    return redirect(url_for('serve_report', lang_code=default_lang_for_redirect, analysis_id=analysis_id), code=301)

@app.route('/<lang_code>/report/<analysis_id>', methods=['GET'])
def serve_report(lang_code, analysis_id):
    if lang_code not in LANGUAGES:
        # Redirecting to 'en' version of this report:
        return redirect(url_for('serve_report', lang_code=app.config['BABEL_DEFAULT_LOCALE'], analysis_id=analysis_id))
    try:
        db = get_db_client()
        doc_ref = db.collection('analyses').document(analysis_id)
        doc = doc_ref.get()
        if doc.exists:
            report_data = doc.to_dict()
            if 'created_at' in report_data and hasattr(report_data['created_at'], 'isoformat'):
                report_data['created_at'] = report_data['created_at'].isoformat()
            recent_analyses = get_analyses()
            return render_template('report.html', report=report_data, recent_analyses=recent_analyses)
        else:
            return _("Report not found"), 404
    except Exception as e:
        print(f"Error fetching report {analysis_id}: {e}")
        return f"{_('An error occurred')}: {e}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
