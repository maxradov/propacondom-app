import os
from flask import Flask, request, jsonify, render_template, make_response, redirect, url_for, g, current_app
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

# Initialize g.current_lang to None at the beginning of each request.
# This ensures it's always defined.
@app.before_request
def ensure_g():
    g.current_lang = None

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
SUPPORTED_LANGUAGES_IN_URL = list(LANGUAGES.keys())

def get_locale():
    # 1. Use language from URL if available and valid (set in g.current_lang by pull_lang_from_url)
    if hasattr(g, 'current_lang') and g.current_lang in SUPPORTED_LANGUAGES_IN_URL:
        return g.current_lang

    # This part is primarily for determining language for initial redirection
    # from non-prefixed URLs, or if URL lang was invalid.
    # 2. Language code from 'lang' cookie
    lang_code = request.cookies.get('lang')
    if lang_code and lang_code in SUPPORTED_LANGUAGES_IN_URL:
        return lang_code

    # 3. Best match from Accept-Language header
    best_match = request.accept_languages.best_match(SUPPORTED_LANGUAGES_IN_URL)
    if best_match:
        return best_match

    # 4. Fallback to default locale
    return current_app.config['BABEL_DEFAULT_LOCALE']

babel = Babel(app, locale_selector=get_locale)

@app.url_value_preprocessor
def pull_lang_from_url(endpoint, values):
    # This preprocessor runs *after* the route is matched but *before* the view function.
    # 'values' contains the matched URL parameters.
    if values and 'lang' in values:
        url_lang_candidate = values['lang']
        if url_lang_candidate in SUPPORTED_LANGUAGES_IN_URL:
            g.current_lang = url_lang_candidate # Set if valid
        else:
            # Invalid lang in URL (e.g., /xx/report/123)
            # We will redirect to the default language version of the same page.
            # Set g.current_lang to the default so get_locale() called by Babel picks it up for this cycle if no redirect happens.
            g.current_lang = current_app.config['BABEL_DEFAULT_LOCALE']

            # Construct the path for redirection
            path_parts = request.path.split('/') # Example: ['', 'xx', 'report', '123']
            if len(path_parts) > 1 and path_parts[1] == url_lang_candidate: # path_parts[0] is empty
                path_parts[1] = g.current_lang # Replace 'xx' with 'en'
                new_path = "/".join(path_parts)
                if request.query_string:
                    new_path += f"?{request.query_string.decode('utf-8')}"

                # Returning a response from url_value_preprocessor short-circuits the request
                # and issues the response (the redirect).
                return redirect(new_path, code=302) # Temporary redirect

@app.before_request
def global_before_request_handler():
    # This runs after ensure_g and after url_value_preprocessor for matched routes.

    # 1. Domain redirection (if necessary)
    old_domain = "propacondom.com"
    new_domain = "factchecking.pro"
    if request.host.startswith(old_domain):
        new_url = f"https://{new_domain}{request.full_path}"
        if new_url.endswith('?'): # Avoid trailing '?' if no query string
            new_url = new_url[:-1]
        return redirect(new_url, code=301) # Permanent redirect for domain change

    # 2. Language prefix redirection for non-API, non-static routes
    # This section handles requests that either:
    #   a) Don't have a language prefix (e.g., `/` or `/report/xyz`)
    #   b) Matched a route *without* a `<lang>` parameter (e.g. old @app.route('/'), @app.route('/report/...'))
    # It relies on g.current_lang NOT being set by pull_lang_from_url (which means no valid lang prefix was in the URL that matched a <lang> rule).

    if not g.current_lang and request.endpoint and not request.path.startswith('/api/') and request.endpoint != 'static':
        # If no valid 'lang' was extracted from the URL by pull_lang_from_url (g.current_lang is None or was reset)
        # And it's a user-facing page (not API, not static)
        # Then we need to redirect to a language-prefixed URL.

        # Determine the preferred language for redirection (uses cookie, Accept-Language, then default)
        preferred_lang_for_redirect = get_locale() # This will not use g.current_lang as it's not valid/set
                                                 # but will use cookie/header/default.

        # Construct the new path with the determined language prefix.
        # request.path already includes the leading slash.
        # For /report/xyz, request.path is /report/xyz. We want /<lang>/report/xyz
        # For /, request.path is /. We want /<lang>/

        current_path = request.path
        if current_path == '/':
            new_path = f"/{preferred_lang_for_redirect}"
        else:
            new_path = f"/{preferred_lang_for_redirect}{current_path}"

        if request.query_string:
            new_path += f"?{request.query_string.decode('utf-8')}"

        return redirect(new_path, code=302) # Temporary redirect

class FlaskTask(celery_app.Task):
    def __call__(self, *args, **kwargs):
        with app.app_context():
            return self.run(*args, **kwargs)

celery_app.Task = FlaskTask

@app.context_processor
def inject_conf_var():
    # g.current_lang should be reliably set by pull_lang_from_url if a valid lang is in the URL.
    # If not (e.g. during initial redirect, or if URL had no lang prefix),
    # get_locale() determines the language Babel will use.
    # This ensures CURRENT_LANG in templates is consistent with what Babel is using.

    # If g.current_lang was set from a valid URL segment, it's the source of truth.
    # Otherwise, get_locale() provides the language determined by cookie/header/default, which Babel also uses.
    template_current_lang = g.current_lang if hasattr(g, 'current_lang') and g.current_lang in SUPPORTED_LANGUAGES_IN_URL else get_locale()

    return dict(
        LANGUAGES=LANGUAGES,
        CURRENT_LANG=template_current_lang, # This is the language for the current request rendering
        SUPPORTED_LANGS_FOR_HREFLANG=SUPPORTED_LANGUAGES_IN_URL # For generating all hreflang links
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
                else: claim_info["is_cached"] = False
            else: claim_info["is_cached"] = False
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

# This route will be updated later to handle redirection to new lang-prefixed URLs
@app.route('/set_language/<lang_code_to_set>')
def set_language(lang_code_to_set):
    if lang_code_to_set not in SUPPORTED_LANGUAGES_IN_URL:
        lang_code_to_set = current_app.config['BABEL_DEFAULT_LOCALE']

    # Determine the page to redirect to.
    # Try to redirect to the same page in the new language.
    # For simplicity now, redirect to the new language's home page.
    # This will be improved in a later step.
    # TODO: Make this redirect smarter, to the same page if possible.
    # One way: pass current endpoint and args, or parse request.referrer.

    # A more robust solution is needed here. For now, redirecting to home.
    # The plan includes updating this route properly.
    # We need to use url_for with the new language.
    # If request.referrer is from the same site, we can try to adapt it.

    # Simple redirect to new language's homepage for now
    # This will be replaced by a more intelligent redirect that keeps the user on the same page.
    redirect_url = url_for('serve_index', lang=lang_code_to_set) # Assumes serve_index will take 'lang'

    response = make_response(redirect(redirect_url))
    response.set_cookie('lang', lang_code_to_set, max_age=60*60*24*365*2) # Expires in 2 years
    return response

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Input is required"}), 400
    user_input = data['url']
    # For API, language can be passed in payload or defaults via get_locale (cookie/header based)
    # API calls are not prefixed, so g.current_lang won't be set from URL.
    target_lang = data.get('lang', get_locale())
    if target_lang not in SUPPORTED_LANGUAGES_IN_URL:
        target_lang = current_app.config['BABEL_DEFAULT_LOCALE']
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

# These routes will be updated to include <lang> prefix
@app.route('/', methods=['GET'])
def serve_index_legacy(): # Renamed temporarily
    # This route will be handled by the redirection logic in global_before_request_handler
    # Or, more directly, we can define it as /<lang>/ and have a separate / route for redirect.
    # For now, global_before_request_handler should redirect this.
    # The actual content serving will happen via the new /<lang>/ route.
    # So, this function might not even be called if redirection works as planned.
    # If it is called, it means redirection didn't happen, which is an issue.
    # Let's assume it's a fallback or will be removed/replaced.
    # For safety, rendering a simple message or relying on redirect.
    # The redirect logic in global_before_request_handler should catch this.
    # If we reach here, something is not right with the redirect setup.
    # This will be replaced by a redirecting route shortly.
    return "Redirecting..."


@app.route('/report/<analysis_id>', methods=['GET'])
def serve_report_legacy(analysis_id): # Renamed temporarily
    # Similar to serve_index_legacy, this should be caught by redirection.
    # This will be replaced by a redirecting route shortly.
    return "Redirecting..."


# New user-facing routes with language prefix will be added in the next step.
# For example:
# @app.route('/<string:lang>/', methods=['GET'])
# def serve_index(lang):
#     ...
# @app.route('/<string:lang>/report/<analysis_id>', methods=['GET'])
# def serve_report(lang, analysis_id):
#     ...

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
