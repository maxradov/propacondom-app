import os
import logging # Ensure logging is imported at the top
from flask import Flask, request, jsonify, render_template, make_response, redirect, url_for
from flask_cors import CORS
from celery.result import AsyncResult
from datetime import datetime
from google.cloud.firestore_v1.query import Query
from flask_babel import Babel, _ # _ is imported here but Jinja uses the one Babel puts in env
from datetime import datetime, timezone, timedelta
from constants import CACHE_EXPIRATION_DAYS

from celery_init import celery as celery_app
from tasks import get_db_client

app = Flask(__name__)
CORS(app)

# Configure logger early based on environment variable
LOG_LEVEL_STR = os.environ.get('FLASK_LOG_LEVEL', 'INFO').upper()
numeric_log_level = getattr(logging, LOG_LEVEL_STR, logging.INFO)
app.logger.setLevel(numeric_log_level)
# You might want to add a handler if running in an environment where Flask/Gunicorn doesn't add one by default
# For example, to ensure logs go to stdout/stderr:
if not app.logger.handlers:
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    app.logger.addHandler(stream_handler)
app.logger.info(f"Flask logger initialized. Level set to: {LOG_LEVEL_STR} ({numeric_log_level})")


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
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'

babel = Babel(app)

@babel.localeselector
def get_locale_for_babel():
    app.logger.debug(f"get_locale_for_babel CALLED. request.endpoint: {request.endpoint}, request.view_args: {request.view_args}")
    lang_to_return = None
    if request.view_args and 'lang_code' in request.view_args:
        url_lang_code = request.view_args.get('lang_code')
        if url_lang_code in LANGUAGES:
            app.logger.info(f"get_locale_for_babel: Returning lang_code from URL: {url_lang_code}")
            lang_to_return = url_lang_code
        else:
            app.logger.warning(f"get_locale_for_babel: Invalid lang_code in URL: {url_lang_code}")
    else:
        app.logger.debug("get_locale_for_babel: No 'lang_code' in request.view_args.")

    if not lang_to_return:
        cookie_lang_code = request.cookies.get('lang')
        if cookie_lang_code in LANGUAGES:
            app.logger.info(f"get_locale_for_babel: Returning lang_code from cookie: {cookie_lang_code}")
            lang_to_return = cookie_lang_code
        else:
            if cookie_lang_code:
                app.logger.debug(f"get_locale_for_babel: Invalid lang_code in cookie: {cookie_lang_code}")
            else:
                app.logger.debug("get_locale_for_babel: No lang cookie found.")

    if not lang_to_return:
        supported_langs = list(LANGUAGES.keys())
        best_match = request.accept_languages.best_match(supported_langs)
        if best_match:
            app.logger.info(f"get_locale_for_babel: Returning lang_code from browser: {best_match}")
            lang_to_return = best_match
        else:
            app.logger.debug("get_locale_for_babel: No suitable lang in accept_languages.")

    if not lang_to_return:
        default_locale = app.config['BABEL_DEFAULT_LOCALE']
        app.logger.info(f"get_locale_for_babel: Returning default: {default_locale}")
        lang_to_return = default_locale

    if lang_to_return is None:
        app.logger.error("CRITICAL: get_locale_for_babel is None before final return! Defaulting to 'en'.")
        return 'en'
    app.logger.info(f"get_locale_for_babel: FINAL determined locale: {lang_to_return}")
    return lang_to_return

@app.context_processor
def inject_conf_var():
    current_template_lang = None
    if request.view_args and 'lang_code' in request.view_args:
        lc = request.view_args['lang_code']
        if lc in LANGUAGES:
            current_template_lang = lc

    if not current_template_lang:
        current_template_lang = get_locale_for_babel()

    app.logger.debug(f"inject_conf_var: CURRENT_LANG for template: {current_template_lang}")
    return dict(
        LANGUAGES=LANGUAGES,
        CURRENT_LANG=current_template_lang
    )

# --- Rest of the app.py code ---
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
        if 'extracted_claims' not in data:
            db = get_db_client()
            doc = db.collection('analyses').document(analysis_id).get()
            if doc.exists:
                doc_data = doc.to_dict()
                data['extracted_claims'] = doc_data.get('extracted_claims', [])
            else:
                data['extracted_claims'] = []
        return jsonify(data)
    elif status == 'PENDING_SELECTION':
        claims_ref = db.collection('claims')
        cache_expiry_date = datetime.now(timezone.utc) - timedelta(days=CACHE_EXPIRATION_DAYS)
        claims_for_selection = []
        for claim in data.get("extracted_claims", []):
            claim_hash = claim["hash"]
            claim_text = claim["text"]
            claim_doc = claims_ref.document(claim_hash).get()
            claim_info = { "hash": claim_hash, "text": claim_text }
            if claim_doc.exists:
                cached_data = claim_doc.to_dict()
                last_checked = cached_data.get('last_checked_at')
                if last_checked and last_checked.replace(tzinfo=timezone.utc) > cache_expiry_date:
                    claim_info["is_cached"] = True
                    claim_info["cached_data"] = { "verdict": cached_data.get("verdict", ""), "last_checked_at": str(last_checked) }
                else:
                    claim_info["is_cached"] = False
            else:
                claim_info["is_cached"] = False
            claims_for_selection.append(claim_info)
        return jsonify({
            "status": "PENDING_SELECTION", "claims_for_selection": claims_for_selection,
            "video_title": data.get("video_title") or data.get("title") or "",
            "thumbnail_url": data.get("thumbnail_url", ""), "source_url": data.get("source_url", ""),
            "id": analysis_id, "input_type": data.get("input_type", "youtube")
        })
    else:
        return jsonify({'error': 'Analysis not complete.'}), 400

@app.route('/api/fact_check_selected', methods=['POST'])
def fact_check_selected():
    data = request.get_json()
    if not data or 'analysis_id' not in data or 'selected_claims_data' not in data:
        return jsonify({"error": "analysis_id and a list of selected_claims_data are required"}), 400
    analysis_id = data['analysis_id']
    selected_claims = data['selected_claims_data']
    task = celery_app.send_task('tasks.fact_check_selected', args=[analysis_id, selected_claims])
    return jsonify({"task_id": task.id}), 202

@app.route('/set_language/<target_lang_code>')
def set_language(target_lang_code):
    if target_lang_code not in LANGUAGES:
        target_lang_code = app.config['BABEL_DEFAULT_LOCALE']
    redirect_url = url_for('serve_index', lang_code=target_lang_code)
    if request.referrer:
        try:
            referrer_path = request.referrer.split(request.host_url, 1)[-1]
            path_parts = referrer_path.strip('/').split('/')
            if len(path_parts) > 0 and path_parts[0] in LANGUAGES:
                matched_endpoint = None
                view_args = {'lang_code': target_lang_code}
                if len(path_parts) == 1:
                    matched_endpoint = 'serve_index'
                elif len(path_parts) > 2 and path_parts[1] == 'report':
                    matched_endpoint = 'serve_report'
                    view_args['analysis_id'] = path_parts[2]
                if matched_endpoint:
                    try:
                        redirect_url = url_for(matched_endpoint, **view_args)
                    except Exception as e:
                        app.logger.warning(f"Error building URL for {matched_endpoint} with {view_args}: {e}")
                else:
                    app.logger.info(f"Referrer path '{referrer_path}' did not match known prefixed patterns, redirecting to target lang homepage.")
            else:
                app.logger.info(f"Referrer path '{referrer_path}' does not start with a known lang code, redirecting to target lang homepage.")
        except Exception as e:
            app.logger.error(f"Error processing referrer for set_language: {e}")
    response = make_response(redirect(redirect_url))
    response.set_cookie('lang', target_lang_code, max_age=60*60*24*365*2)
    return response

@app.route('/', methods=['GET'])
def root_redirect():
    determined_lang_code = app.config['BABEL_DEFAULT_LOCALE']
    cookie_lang = request.cookies.get('lang')
    if cookie_lang and cookie_lang in LANGUAGES:
        determined_lang_code = cookie_lang
    else:
        browser_lang = request.accept_languages.best_match(list(LANGUAGES.keys()))
        if browser_lang:
            determined_lang_code = browser_lang
    app.logger.info(f"Root redirect: determined lang_code {determined_lang_code}, redirecting to serve_index.")
    return redirect(url_for('serve_index', lang_code=determined_lang_code))

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Input is required"}), 400
    user_input = data['url']
    target_lang = data.get('lang', get_locale_for_babel())
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
        app.logger.error(f"Error getting task status for {task_id}: {e}")
        return jsonify({'status': 'FAILURE', 'result': 'Could not retrieve task status from backend.'}), 500

def get_analyses(last_timestamp_str=None):
    db = get_db_client()
    query = db.collection('analyses').order_by('created_at', direction=Query.DESCENDING)
    if last_timestamp_str and isinstance(last_timestamp_str, str) and last_timestamp_str.strip():
        try:
            last_timestamp = datetime.fromisoformat(last_timestamp_str)
            query = query.start_after({'created_at': last_timestamp})
        except ValueError:
            app.logger.warning(f"Warning: Could not parse timestamp: '{last_timestamp_str}'")
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
        app.logger.error(f"Error in /api/get_recent_analyses: {e}")
        return jsonify({"error": "Failed to fetch more analyses"}), 500

@app.route('/<lang_code>/', methods=['GET'])
def serve_index(lang_code):
    if lang_code not in LANGUAGES:
        app.logger.warning(f"serve_index: Invalid lang_code '{lang_code}', redirecting to default.")
        return redirect(url_for('serve_index', lang_code=app.config['BABEL_DEFAULT_LOCALE']))
    try:
        recent_analyses = get_analyses()
        url_param = request.args.get('url', '')
        return render_template("index.html", recent_analyses=recent_analyses, initial_url=url_param)
    except Exception as e:
        app.logger.error(f"Error in serve_index for lang {lang_code}: {e}", exc_info=True)
        return render_template("index.html", recent_analyses=[], initial_url='')

@app.route('/report/<analysis_id>', methods=['GET'])
def redirect_old_report(analysis_id):
    default_lang_for_redirect = app.config['BABEL_DEFAULT_LOCALE']
    cookie_lang = request.cookies.get('lang')
    if cookie_lang and cookie_lang in LANGUAGES:
        default_lang_for_redirect = cookie_lang
    else:
        browser_lang = request.accept_languages.best_match(list(LANGUAGES.keys()))
        if browser_lang:
            default_lang_for_redirect = browser_lang
    app.logger.info(f"Redirecting old report URL /report/{analysis_id} to /{(default_lang_for_redirect)}/report/{analysis_id}")
    return redirect(url_for('serve_report', lang_code=default_lang_for_redirect, analysis_id=analysis_id), code=301)

@app.route('/<lang_code>/report/<analysis_id>', methods=['GET'])
def serve_report(lang_code, analysis_id):
    if lang_code not in LANGUAGES:
        app.logger.warning(f"serve_report: Invalid lang_code '{lang_code}' for report {analysis_id}, redirecting to default.")
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
        app.logger.error(f"Error in serve_report for lang {lang_code}, report {analysis_id}: {e}", exc_info=True)
        return f"{_('An error occurred')}: {e}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
