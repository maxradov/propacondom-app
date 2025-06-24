import os
import re
import json
import datetime
import requests
import hashlib
import google.generativeai as genai
from celery import Celery
from google.cloud import firestore

from celery_init import celery

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
SEARCHAPI_KEY = os.environ.get('SEARCHAPI_KEY')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
SEARCH_ENGINE_ID = os.environ.get('SEARCH_ENGINE_ID')

db = None
model = None

def get_db_client():
    global db
    if db is None:
        db = firestore.Client()
    return db

def get_gemini_model():
    global model
    if model is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not configured.")
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
    return model

def is_youtube_url(value):
    return bool(re.search(r'(youtu\.be/|youtube\.com/)', value, re.IGNORECASE))

def is_url(value):
    return bool(re.match(r'https?://[^\s]+', value, re.IGNORECASE))

def get_video_id(url):
    if not url: return None
    regex = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/|\/live\/|googleusercontent\.com\/youtube\.com\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None

def get_text_hash(text):
    # Возвращает стабильный 16-символьный sha1-хеш
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]

celery.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Europe/Sofia',
    enable_utc=True,
    imports=('tasks',)
)

@celery.task(bind=True, name='tasks.fact_check', time_limit=900)
def fact_check(self, user_input, target_lang='en'):
    if is_youtube_url(user_input):
        return analyze_youtube_video(self, user_input, target_lang)
    elif is_url(user_input):
        return analyze_web_url(self, user_input, target_lang)
    else:
        return analyze_free_text(self, user_input, target_lang, user_text=user_input)

def analyze_youtube_video(self, video_url, target_lang='en'):
    local_db = get_db_client()
    gemini_model = get_gemini_model()

    if not SEARCHAPI_KEY: raise ValueError("SEARCHAPI_KEY for subtitles not found.")
    if not GOOGLE_API_KEY: raise ValueError("GOOGLE_API_KEY for search not found.")
    if not SEARCH_ENGINE_ID: raise ValueError("SEARCH_ENGINE_ID for search not found.")

    video_id = get_video_id(video_url)
    if not video_id: raise ValueError(f"Could not extract video ID from URL: {video_url}")

    analysis_id = f"{video_id}_{target_lang}"
    doc_ref = local_db.collection('analyses').document(analysis_id)
    doc = doc_ref.get()
    if doc.exists:
        cached_data = doc.to_dict()
        if 'created_at' in cached_data and hasattr(cached_data['created_at'], 'isoformat'):
            cached_data['created_at'] = cached_data['created_at'].isoformat()
            cached_data["id"] = doc_ref.id
            print(f"=== Returning cached result: {cached_data}")
        return cached_data

    self.update_state(state='PROGRESS', meta={'status_message': 'Fetching video details...'})
    params_details = {'engine': 'youtube_video', 'video_id': video_id, 'api_key': SEARCHAPI_KEY}
    details_response = requests.get('https://www.searchapi.io/api/v1/search', params=params_details)
    details_response.raise_for_status()
    details_data = details_response.json()
    video_data = details_data.get('video')
    video_title, thumbnail_url = (video_data.get('title', 'Title Not Found'), video_data.get('thumbnail', '')) if video_data else ('Title Not Found', '')

    self.update_state(state='PROGRESS', meta={'status_message': 'Checking available languages...'})
    params_list_langs = {'engine': 'youtube_transcripts', 'video_id': video_id, 'api_key': SEARCHAPI_KEY}
    metadata = requests.get('https://www.searchapi.io/api/v1/search', params=params_list_langs).json()
    available_langs = [lang.get('lang') for lang in metadata.get('available_languages', []) if lang.get('lang')]
    if not available_langs: raise ValueError("Subtitles not found.")
    priority_langs = [target_lang, 'en', 'ru', 'uk']
    detected_lang = next((lang for lang in priority_langs if lang in available_langs), available_langs[0])
    self.update_state(state='PROGRESS', meta={'status_message': f'Extracting subtitles ({detected_lang})...'})
    params_get_transcript = {'engine': 'youtube_transcripts', 'video_id': video_id, 'lang': detected_lang, 'api_key': SEARCHAPI_KEY}
    transcript_data = requests.get('https://www.searchapi.io/api/v1/search', params=params_get_transcript).json()
    if not transcript_data.get('transcripts'): raise ValueError(f"API did not return subtitles for '{detected_lang}'.")
    clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])

    return analyze_free_text(
        self, clean_text, target_lang,
        title=video_title,
        thumbnail_url=thumbnail_url,
        source_url=video_url,
        analysis_id=analysis_id,
        input_type="youtube"
    )

def analyze_web_url(self, url, target_lang='en'):
    import bs4
    try:
        self.update_state(state='PROGRESS', meta={'status_message': 'Downloading web page...'})
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        html = response.text
        soup = bs4.BeautifulSoup(html, "html.parser")
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        if not text or len(text) < 200:
            raise ValueError("No readable content found on the page.")
        text = text[:10000]
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        thumbnail_url = ""
        # Для сайта используем hash текста как ключ
        analysis_id = f"text_{get_text_hash(text)}_{target_lang}"
        return analyze_free_text(
            self, text, target_lang,
            title=title,
            thumbnail_url=thumbnail_url,
            source_url=url,
            analysis_id=analysis_id,
            input_type="url"
        )
    except Exception as e:
        return {"error": f"Could not retrieve or parse the site: {str(e)}"}

def analyze_free_text(self, text, target_lang='en', title=None, thumbnail_url=None, source_url=None, analysis_id=None, input_type="text", user_text=None):
    local_db = get_db_client()
    gemini_model = get_gemini_model()

    self.update_state(state='PROGRESS', meta={'status_message': 'Analyzing text...'})

    # Для обычного текста, если не передали analysis_id, формируем его:
    if not analysis_id:
        analysis_id = f"text_{get_text_hash(text)}_{target_lang}"
    doc_ref = local_db.collection('analyses').document(analysis_id)
    doc = doc_ref.get()
    if doc.exists:
        cached_data = doc.to_dict()
        if 'created_at' in cached_data and hasattr(cached_data['created_at'], 'isoformat'):
            cached_data['created_at'] = cached_data['created_at'].isoformat()
            cached_data["id"] = doc_ref.id
            print(f"=== Returning cached result: {cached_data}")
        return cached_data

    prompt_claims = f"""Analyze the following text. Extract 5 main factual claims that can be verified.
Focus on specific, checkable statements. Present them as a numbered list. IMPORTANT: The claims must be written in {target_lang}.
Text: --- {text} ---"""
    response_claims = gemini_model.generate_content(prompt_claims)
    claims_list = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip() and re.match(r'^\d+\.', line)]
    if not claims_list:
        raise ValueError("AI did not return claims in the expected format.")

    self.update_state(state='PROGRESS', meta={'status_message': f'Extracted {len(claims_list)} statements. Fact-checking...'})

    all_results = []
    for claim in claims_list:
        search_params = {'q': claim, 'key': GOOGLE_API_KEY, 'cx': SEARCH_ENGINE_ID, 'num': 4}
        search_response = requests.get("https://www.googleapis.com/customsearch/v1", params=search_params)
        search_results = search_response.json().get('items', [])
        search_context = "No relevant search results found."
        if search_results:
            search_context = ""
            for i, result in enumerate(search_results):
                search_context += f"Source {i+1}: URL: {result.get('link')}, Snippet: {result.get('snippet')}\n"

        prompt_fc = f"""
        Based on the provided web search results, fact-check the following claim.
        Claim: "{claim}"

        Web Search Results:
        ---
        {search_context}
        ---

        Your task is to return a single JSON object with these keys: "claim", "verdict", "confidence_percentage", "explanation", "sources".
        - The "claim" value MUST be the original claim provided above.
        - The "verdict" MUST be one of: "True", "False", "Misleading", "Partly True", "Unverifiable".
        - The "explanation" MUST be a concise, neutral summary of the evidence, written STRICTLY in the following language: {target_lang}.
        - The "sources" MUST be a list of the URLs from the search results that you used for your conclusion.
        - Your entire response must be ONLY a single JSON object.
        """
        fc_response = gemini_model.generate_content(prompt_fc)
        match = re.search(r'\{.*\}', fc_response.text, re.DOTALL)
        if not match:
            result_item = {"claim": claim, "verdict": "Unverifiable", "confidence_percentage": 0, "explanation": "AI failed to provide a valid analysis.", "sources": []}
        else:
            result_item = json.loads(match.group(0))
        all_results.append(result_item)

    self.update_state(state='PROGRESS', meta={'status_message': 'Generating final report...'})

    verdict_counts = {"True": 0, "False": 0, "Misleading": 0, "Partly True": 0, "Unverifiable": 0}
    confidence_sum = 0
    claims_with_confidence = 0
    for res in all_results:
        verdict = res.get("verdict", "Unverifiable")
        if verdict in verdict_counts:
            verdict_counts[verdict] += 1
        else:
            verdict_counts["Unverifiable"] += 1
        if "confidence_percentage" in res and isinstance(res["confidence_percentage"], int):
            confidence_sum += res["confidence_percentage"]
            claims_with_confidence += 1

    average_confidence = round(confidence_sum / claims_with_confidence) if claims_with_confidence > 0 else 0
    true_verdicts = verdict_counts.get("True", 0)
    total_verdicts = len(all_results)
    confirmed_credibility = round((true_verdicts / total_verdicts) * 100) if total_verdicts > 0 else 0

    summary_context = [{"claim": res.get("claim"), "verdict": res.get("verdict")} for res in all_results]

    summary_prompt = f"""Analyze the fact-checking results. Return a single, clean JSON object.
It must have these keys: "overall_verdict", "overall_assessment", and "key_points".
- "overall_verdict" must be one of: "Mostly True", "Mostly False", "Mixed Veracity", "Largely Unverifiable".
- "overall_assessment" must be a neutral, one-paragraph summary written STRICTLY in the following language: {target_lang}.
- "key_points" must be a JSON array of simple STRINGS, and each string must be written STRICTLY in the following language: {target_lang}.
Data: {json.dumps(summary_context, ensure_ascii=False)}
"""
    final_report_response = gemini_model.generate_content(summary_prompt)
    summary_match = re.search(r'\{.*\}', final_report_response.text, re.DOTALL)
    if summary_match:
        summary_data = json.loads(summary_match.group(0))
    else:
        summary_data = {}

    data_to_save_in_db = {
        "input_type": input_type,
        "video_url": source_url or "",
        "video_title": title or "Text Analysis",
        "thumbnail_url": thumbnail_url or "",
        "summary_data": summary_data,
        "verdict_counts": verdict_counts,
        "average_confidence": average_confidence,
        "confirmed_credibility": confirmed_credibility,
        "detailed_results": all_results,
        "created_at": firestore.SERVER_TIMESTAMP
    }
    if input_type == "text" and user_text is not None:
        data_to_save_in_db["user_text"] = user_text
        
    doc_ref.set(data_to_save_in_db)
    data_to_return = data_to_save_in_db.copy()
    data_to_return["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data_to_return["id"] = doc_ref.id
    print(f"=== Returning result from analyze_free_text: {data_to_return}")
    return data_to_return
