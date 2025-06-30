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

# Mapping for og:locale meta tag
OG_LOCALE_MAPPING = {
    'en': 'en_US',
    'es': 'es_ES',
    'zh': 'zh_CN',
    'hi': 'hi_IN',
    'fr': 'fr_FR',
    'ar': 'ar_AE', # Using AE as a common one, adjust if specific dialect is preferred
    'bn': 'bn_BD',
    'ru': 'ru_RU',
    'uk': 'uk_UA',
    'pt': 'pt_PT', # Or pt_BR if Brazilian Portuguese is the primary target
    'de': 'de_DE'
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
    if request.path in ['/robots.txt', '/sitemap.xml']:
        return None
    # 2. Language prefix redirection for non-API, non-static routes
    # This section handles requests that either:
    #   a) Don't have a language prefix (e.g., `/` or `/report/xyz`)
    #   b) Matched a route *without* a `<lang>` parameter (e.g. old @app.route('/'), @app.route('/report/...'))
    # It relies on g.current_lang NOT being set by pull_lang_from_url (which means no valid lang prefix was in the URL that matched a <lang> rule).
    first_segment = request.path.lstrip('/').split('/', 1)[0]
    if first_segment in SUPPORTED_LANGUAGES_IN_URL:
        # Уже есть lang-префикс, ничего не делаем!
        return None
    if not g.current_lang and request.endpoint and not request.path.startswith('/api/') and request.endpoint != 'static':
        preferred_lang_for_redirect = get_locale()
        current_path = request.path
        if current_path == '/':
            new_path = f"/{preferred_lang_for_redirect}/"
        else:
            new_path = f"/{preferred_lang_for_redirect}{current_path}"
        if request.query_string:
            new_path += f"?{request.query_string.decode('utf-8')}"
        return redirect(new_path, code=302)

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

    def hreflang_url(endpoint, view_args, lang):
        args = dict(view_args) if view_args else {}
        args['lang'] = lang
        # Ensure absolute URLs for hreflang tags
        return url_for(endpoint, _external=True, **args)
    return dict(
        LANGUAGES=LANGUAGES,
        CURRENT_LANG=template_current_lang,
        SUPPORTED_LANGS_FOR_HREFLANG=SUPPORTED_LANGUAGES_IN_URL,
        hreflang_url=hreflang_url,
        OG_LOCALE_MAPPING=OG_LOCALE_MAPPING
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

from urllib.parse import urlparse, urljoin # For intelligent language switching

# This route will be updated later to handle redirection to new lang-prefixed URLs
@app.route('/set_language/<lang_code_to_set>')
def set_language(lang_code_to_set):
    if lang_code_to_set not in SUPPORTED_LANGUAGES_IN_URL:
        lang_code_to_set = current_app.config['BABEL_DEFAULT_LOCALE']

    redirect_url = None

    # Try to redirect to the same page in the new language using request.referrer
    if request.referrer:
        referrer_url = urlparse(request.referrer)
        # Ensure the referrer is from the same site (same netloc)
        # Use request.host for current site's host, which includes port if non-standard
        if referrer_url.netloc == request.host:
            try:
                # Match the referrer's path to an endpoint and its arguments
                # We need to strip the language prefix from the referrer path if it exists
                # before trying to match, as our routes don't have the lang prefix in the rule itself.
                # However, our current routes like /<lang>/ are defined with <lang>.
                # Let's test matching with the full path first.
                # The url_map matching expects the path part of the URL.

                # The adapter needs the environment; use request.environ
                url_adapter = current_app.url_map.bind_to_environ(request.environ)
                # The path in referrer_url.path might include a language prefix
                # For example, /en/some/page or /some/page if it was an old URL
                # The matching should handle this.

                # Let's adjust the referrer path slightly to ensure it's clean
                # and matches how Flask might expect it for matching.
                path_to_match = referrer_url.path

                # If current app uses an APPLICATION_ROOT, referrer path might need adjustment.
                # Assuming no complex APPLICATION_ROOT for now.

                matched_endpoint, view_args = url_adapter.match(path_to_match)

                if matched_endpoint and matched_endpoint not in ['static', 'set_language']:
                    # Successfully matched the referrer to an endpoint
                    # Now, build the new URL with the new language
                    new_view_args = view_args.copy()
                    new_view_args['lang'] = lang_code_to_set # Set/override the language

                    # Remove query parameters from original view_args if they were captured in path
                    # and re-add them from the original referrer's query string to avoid duplication or loss.
                    # However, view_args from match() only contains path parameters.
                    # Query parameters from referrer_url.query should be appended separately if needed.
                    # For now, url_for will build the path; query params are usually separate.
                    # If a specific page needs certain query params preserved, that's more complex.
                    # For this implementation, we'll redirect to the base path of the matched page.

                    redirect_url = url_for(matched_endpoint, **new_view_args)

                    # Preserve query string from original referrer if any
                    if referrer_url.query:
                        redirect_url = f"{redirect_url}?{referrer_url.query}"

            except Exception as e:
                # Exception could be werkzeug.routing.exceptions.NotFound if path doesn't match
                # Or other issues. In any case, fall back to homepage.
                print(f"Could not match referrer path '{referrer_url.path}' or build URL: {e}")
                redirect_url = None # Ensure fallback

    if not redirect_url:
        # Fallback: redirect to the new language's homepage
        redirect_url = url_for('serve_index', lang=lang_code_to_set)

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



@app.route('/<lang>/', methods=['GET'])
def serve_index(lang):
    print(f"SERVE_INDEX: lang={lang}, path={request.path}")
    try:
        recent_analyses = get_analyses()
        url_param = request.args.get('url', '')
        return render_template("index.html", recent_analyses=recent_analyses, initial_url=url_param)
    except Exception as e:
        print(f"Error fetching initial analyses: {e}")
        return render_template("index.html", recent_analyses=[], initial_url='')

@app.route('/<lang>/report/<analysis_id>', methods=['GET'])
def serve_report(lang, analysis_id):
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

@app.route('/report/<analysis_id>', methods=['GET'])
def serve_report_legacy(analysis_id):
    preferred_lang = get_locale()
    return redirect(url_for('serve_report', lang=preferred_lang, analysis_id=analysis_id), code=302)

@app.route('/', methods=['GET'])
def root_redirect():
    """
    Redirects users from the root path `/` to a language-specific homepage.
    - Bots are redirected to the default language (e.g., /en/).
    - Regular users are redirected based on their cookie or Accept-Language header.
    This prevents bots from being redirected based on their origin's Accept-Language
    if it doesn't match the site's primary content strategy for indexing.
    """
    user_agent_string = request.user_agent.string.lower()
    bot_keywords = [
        'googlebot', 'bingbot', 'slurp', 'duckduckbot', 'baiduspider',
        'yandexbot', 'sogou', 'exabot', 'facebot', 'facebookexternalhit', # facebook
        'twitterbot', 'linkedinbot', 'applebot', 'pinterest'
    ]

    is_bot_request = any(keyword in user_agent_string for keyword in bot_keywords)

    if is_bot_request:
        # For bots, always redirect to the default language homepage (e.g., English)
        # This ensures consistent indexing.
        default_lang_for_bots = app.config.get('BABEL_DEFAULT_LOCALE', 'en')
        # Add a comment indicating this behavior for future reference.
        # Comment: Bots visiting root are redirected to the default language version.
        print(f"Bot detected: {user_agent_string}, redirecting to /{default_lang_for_bots}/") # For logging
        return redirect(url_for('serve_index', lang=default_lang_for_bots), code=302)
    else:
        # For regular users, determine preferred language via get_locale()
        # (which checks URL, then cookie, then Accept-Language header, then default)
        preferred_lang = get_locale()
        return redirect(url_for('serve_index', lang=preferred_lang), code=302)

# Sitemap generation
@app.route('/sitemap.xml')
def sitemap():
    """
    Generates the sitemap.xml dynamically.
    Includes homepage and a generic report page for each supported language.
    Provides alternate hreflang links for each URL.
    """
    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">'
    ]

    # Define pages to include in the sitemap (endpoint name and view_args if any)
    # For dynamic parts like <analysis_id>, we list the general structure.
    # Actual sitemaps might list specific important URLs, but for a dynamic app,
    # this approach is more manageable for on-the-fly generation.
    pages_to_map = [
        ('serve_index', {}),
        # Add other key public page endpoints here if they exist
        # For example, if there was a generic '/<lang>/reports/' page:
        # ('serve_reports_overview', {})
        # For now, we don't have a generic reports list page, only individual ones.
        # We will represent the report section by its structure.
        # A true sitemap for individual reports would require fetching all report IDs.
        # For simplicity, we will point to the base structure, implying /<lang>/report/*
        # This is a common simplification. Alternatively, one could list a few recent reports.
        # For this exercise, we will only add a conceptual entry for reports.
        # In a real-world scenario, one might list top N reports or use a separate sitemap for reports.
    ]

    # Add a conceptual entry for the report structure
    # This doesn't create a sitemap entry for /<lang>/report/ directly as it's not a page,
    # but signals to search engines that URLs under /<lang>/report/ exist.
    # A more robust way for numerous items is a sitemap index file.
    # For now, we'll list the main index pages.
    # If we had a page like /en/reports (plural) that lists reports, we'd add its endpoint.
    # Since /<lang>/report/<id> is the structure, we'll skip adding a generic /<lang>/report/
    # to the sitemap unless it's an actual browseable page.
    # Let's assume for now `serve_report` is for specific reports and not a generic listing.

    supported_langs = list(LANGUAGES.keys())
    default_lang = app.config['BABEL_DEFAULT_LOCALE'] # 'en'

    for endpoint_name, view_args in pages_to_map:
        for lang_code in supported_langs:
            # Create the primary URL for the current language
            loc_args = view_args.copy()
            loc_args['lang'] = lang_code
            # Ensure SERVER_NAME is configured for url_for to generate absolute URLs
            # If not, it will generate relative URLs. For sitemaps, absolute URLs are required.
            # Flask's url_for generates absolute URLs if app.config['SERVER_NAME'] is set
            # and _external=True. If not set, we might need to prepend the domain manually.
            # For this exercise, we assume it's configured or will be.
            # A common setup is to set app.config['SERVER_NAME'] = 'factchecking.pro'
            # and app.config['APPLICATION_ROOT'] = '/'
            # and app.config['PREFERRED_URL_SCHEME'] = 'https'
            # For now, let's ensure _external=True and rely on Flask's behavior.
            # It's crucial that the server is configured for this.
            # If request context is available (which it is in a route), _external=True works.

            # Construct URL, ensuring it's absolute
            # For sitemap, we need absolute URLs. url_for with _external=True
            # relies on request context or SERVER_NAME configuration.
            # In a route handler, request context is available.
            current_url = url_for(endpoint_name, _external=True, **loc_args)

            xml_parts.append('  <url>')
            xml_parts.append(f'    <loc>{current_url}</loc>')

            # Add alternate links for all languages
            for alt_lang_code in supported_langs:
                alt_args = view_args.copy()
                alt_args['lang'] = alt_lang_code
                alt_url = url_for(endpoint_name, _external=True, **alt_args)
                xml_parts.append(f'    <xhtml:link rel="alternate" hreflang="{alt_lang_code}" href="{alt_url}"/>')

            # Add x-default link (pointing to the default language version, e.g., English)
            default_args = view_args.copy()
            default_args['lang'] = default_lang
            default_url = url_for(endpoint_name, _external=True, **default_args)
            xml_parts.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{default_url}"/>')

            xml_parts.append('  </url>')

    xml_parts.append('</urlset>')
    sitemap_xml = "\n".join(xml_parts)

    response = make_response(sitemap_xml)
    response.headers["Content-Type"] = "application/xml"
    return response

# robots.txt generation
@app.route('/robots.txt')
def robots_txt():
    """
    Generates the robots.txt file dynamically.
    Allows crawling of language-specific paths and the sitemap.
    Disallows crawling of API endpoints.
    """
    lines = ["User-agent: *"]

    # Allow crawling for each language-prefixed path
    for lang_code in LANGUAGES.keys():
        lines.append(f"Allow: /{lang_code}/")

    # Allow crawling of the sitemap itself
    lines.append("Allow: /sitemap.xml") # Path to the sitemap

    # Disallow API paths
    lines.append("Disallow: /api/")

    # Add other Disallow rules as needed, for example:
    # lines.append("Disallow: /admin/")
    # lines.append("Disallow: /tmp/")

    # Specify the location of the sitemap
    # Ensure this is an absolute URL. url_for with _external=True can be used.
    # This assumes app.config['SERVER_NAME'] and app.config['PREFERRED_URL_SCHEME'] are set.
    sitemap_url = url_for('sitemap', _external=True)
    lines.append(f"Sitemap: {sitemap_url}")

    robots_content = "\n".join(lines)
    response = make_response(robots_content)
    response.headers["Content-Type"] = "text/plain"
    return response


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

