# tasks.py

import os
import re
import json
import hashlib
import requests
from datetime import datetime, timezone, timedelta

import google.generativeai as genai
from celery import Celery
from google.cloud import firestore
import bs4

from constants import MAX_CLAIMS_EXTRACTED, MAX_CLAIMS_TO_CHECK, CACHE_EXPIRATION_DAYS
from celery_init import celery

# --- Конфигурация API и глобальные переменные ---
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
SEARCHAPI_KEY = os.environ.get('SEARCHAPI_KEY')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
SEARCH_ENGINE_ID = os.environ.get('SEARCH_ENGINE_ID')

db = None
model = None

# --- Вспомогательные функции ---

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

def get_claim_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def get_text_hash(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]

def is_youtube_url(value):
    return bool(re.search(r'(youtu\.be/|youtube\.com/)', value, re.IGNORECASE))

def is_url(value):
    return bool(re.match(r'https?://[^\s]+', value, re.IGNORECASE))

def get_video_id(url):
    if not url: return None
    regex = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/|\/live\/|googleusercontent\.com\/youtube\.com\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None


def _process_claims_with_cache(claims_data_from_db):
    """
    НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ.
    Принимает список утверждений, проверяет кэш для каждого в коллекции 'claims'
    и возвращает данные, готовые для отправки на фронтенд.
    """
    local_db = get_db_client()
    claims_ref = local_db.collection('claims')
    cache_expiry_date = datetime.now(timezone.utc) - timedelta(days=CACHE_EXPIRATION_DAYS)
    
    claims_for_frontend = []
    for claim_data in claims_data_from_db:
        claim_hash = claim_data.get("hash")
        claim_text = claim_data.get("text")
        
        claim_doc_ref = claims_ref.document(claim_hash)
        claim_doc = claim_doc_ref.get()
        
        if claim_doc.exists:
            cached_data = claim_doc.to_dict()
            last_checked = cached_data.get('last_checked_at')
            if last_checked and last_checked.replace(tzinfo=timezone.utc) > cache_expiry_date:
                claims_for_frontend.append({
                    "hash": claim_hash, "text": claim_text,
                    "is_cached": True, "cached_data": cached_data
                })
                continue
        
        claims_for_frontend.append({"hash": claim_hash, "text": claim_text, "is_cached": False})
    
    return claims_for_frontend


# --- Основные задачи Celery ---

@celery.task(bind=True, name='tasks.extract_claims', time_limit=300)
def extract_claims(self, user_input, target_lang='en'):
    """Главная точка входа. Определяет тип ввода и запускает соответствующий анализатор."""
    if is_youtube_url(user_input):
        return analyze_youtube_video(self, user_input, target_lang)
    elif is_url(user_input):
        return analyze_web_url(self, user_input, target_lang)
    else:
        return analyze_free_text(self, user_input, target_lang)


def analyze_youtube_video(self, video_url, target_lang='en'):
    """
    ПЕРЕПИСАНА.
    Проверяет наличие существующего анализа. Если его нет - извлекает claims и сохраняет.
    Если есть - сразу возвращает сохраненные claims.
    """
    local_db = get_db_client()
    video_id = get_video_id(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    # Шаг 1: Генерируем ID и проверяем, существует ли анализ в Firestore
    analysis_id = f"{video_id}_{target_lang}"
    analysis_doc_ref = local_db.collection('analyses').document(analysis_id)
    analysis_doc = analysis_doc_ref.get()

    if analysis_doc.exists:
        # --- Логика для ПОВТОРНОГО анализа ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Found existing analysis. Fetching claims...'})
        saved_claims = analysis_doc.to_dict().get('extracted_claims', [])
        claims_for_selection = _process_claims_with_cache(saved_claims)
        return {"id": analysis_id, "claims_for_selection": claims_for_selection}
    else:
        # --- Логика для ПЕРВОГО анализа ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Fetching video details...'})
        # (Код для получения данных с YouTube)
        params_details = {'engine': 'youtube_video', 'video_id': video_id, 'api_key': SEARCHAPI_KEY}
        details_response = requests.get('https://www.searchapi.io/api/v1/search', params=params_details)
        details_data = details_response.json().get('video', {})
        video_title = details_data.get('title', 'Title Not Found')
        thumbnail_url = details_data.get('thumbnail', '')
        
        self.update_state(state='PROGRESS', meta={'status_message': 'Extracting subtitles...'})
        params_get_transcript = {'engine': 'youtube_transcripts', 'video_id': video_id, 'lang': target_lang, 'api_key': SEARCHAPI_KEY}
        transcript_data = requests.get('https://www.searchapi.io/api/v1/search', params=params_get_transcript).json()
        if not transcript_data.get('transcripts'): raise ValueError(f"API did not return subtitles for '{target_lang}'.")
        clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])

        self.update_state(state='PROGRESS', meta={'status_message': 'AI is extracting claims for the first time...'})
        gemini_model = get_gemini_model()
        prompt_claims = f"Analyze the following text. Extract up to {MAX_CLAIMS_EXTRACTED} main factual claims... Text: --- {clean_text[:15000]} ---"
        response_claims = gemini_model.generate_content(prompt_claims)
        claims_list_text = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip()]
        
        claims_for_db = [{"hash": get_claim_hash(text), "text": text} for text in claims_list_text]

        analysis_data = {
            "status": "PENDING_SELECTION", "input_type": "youtube", "source_url": video_url,
            "video_title": video_title, "thumbnail_url": thumbnail_url,
            "extracted_claims": claims_for_db, # Сохраняем утверждения навсегда
            "target_lang": target_lang, "created_at": firestore.SERVER_TIMESTAMP
        }
        analysis_doc_ref.set(analysis_data)
        
        claims_for_selection = _process_claims_with_cache(claims_for_db)
        return {"id": analysis_id, "claims_for_selection": claims_for_selection}


def analyze_web_url(self, url, target_lang='en'):
    """ПЕРЕПИСАНА. Аналогичная логика для URL."""
    # ... (код для получения текста со страницы, как и раньше)
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        soup = bs4.BeautifulSoup(response.text, "html.parser")
        text = soup.get_text(separator=' ', strip=True)
        if not text or len(text) < 200: raise ValueError("No readable content found.")
        title = soup.title.string.strip() if soup.title and soup.title.string else url
    except Exception as e:
        raise ValueError(f"Could not retrieve or parse the site: {str(e)}")

    local_db = get_db_client()
    analysis_id = f"url_{get_text_hash(text)}_{target_lang}"
    analysis_doc_ref = local_db.collection('analyses').document(analysis_id)
    analysis_doc = analysis_doc_ref.get()

    if analysis_doc.exists():
        self.update_state(state='PROGRESS', meta={'status_message': 'Found existing analysis. Fetching claims...'})
        saved_claims = analysis_doc.to_dict().get('extracted_claims', [])
        claims_for_selection = _process_claims_with_cache(saved_claims)
        return {"id": analysis_id, "claims_for_selection": claims_for_selection}
    else:
        self.update_state(state='PROGRESS', meta={'status_message': 'AI is extracting claims for the first time...'})
        gemini_model = get_gemini_model()
        prompt_claims = f"Analyze the following text... Text: --- {text[:15000]} ---"
        response_claims = gemini_model.generate_content(prompt_claims)
        claims_list_text = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip()]

        claims_for_db = [{"hash": get_claim_hash(text), "text": text} for text in claims_list_text]
        analysis_data = {
            "status": "PENDING_SELECTION", "input_type": "url", "source_url": url,
            "video_title": title, "thumbnail_url": "",
            "extracted_claims": claims_for_db,
            "target_lang": target_lang, "created_at": firestore.SERVER_TIMESTAMP
        }
        analysis_doc_ref.set(analysis_data)
        
        claims_for_selection = _process_claims_with_cache(claims_for_db)
        return {"id": analysis_id, "claims_for_selection": claims_for_selection}


def analyze_free_text(self, text, target_lang='en'):
    """ПЕРЕПИСАНА. Аналогичная логика для простого текста."""
    local_db = get_db_client()
    analysis_id = f"text_{get_text_hash(text)}_{target_lang}"
    analysis_doc_ref = local_db.collection('analyses').document(analysis_id)
    analysis_doc = analysis_doc_ref.get()

    if analysis_doc.exists():
        self.update_state(state='PROGRESS', meta={'status_message': 'Found existing analysis. Fetching claims...'})
        saved_claims = analysis_doc.to_dict().get('extracted_claims', [])
        claims_for_selection = _process_claims_with_cache(saved_claims)
        return {"id": analysis_id, "claims_for_selection": claims_for_selection}
    else:
        self.update_state(state='PROGRESS', meta={'status_message': 'AI is extracting claims for the first time...'})
        gemini_model = get_gemini_model()
        prompt_claims = f"Analyze the following text... Text: --- {text[:15000]} ---"
        response_claims = gemini_model.generate_content(prompt_claims)
        claims_list_text = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip()]
        
        claims_for_db = [{"hash": get_claim_hash(text), "text": text} for text in claims_list_text]
        analysis_data = {
            "status": "PENDING_SELECTION", "input_type": "text", "source_url": "",
            "video_title": f"Text Analysis ({text[:20]}...)", "thumbnail_url": "",
            "user_text": text, "extracted_claims": claims_for_db,
            "target_lang": target_lang, "created_at": firestore.SERVER_TIMESTAMP
        }
        analysis_doc_ref.set(analysis_data)
        
        claims_for_selection = _process_claims_with_cache(claims_for_db)
        return {"id": analysis_id, "claims_for_selection": claims_for_selection}

# ==============================================================================
# ЗАДАЧА №2 (Проверка выбранных) - ОСТАЛАСЬ БЕЗ ИЗМЕНЕНИЙ
# ==============================================================================
@celery.task(bind=True, name='tasks.fact_check_selected', time_limit=600)
def fact_check_selected_claims(self, analysis_id, selected_claims_data):
    # (Весь код этой функции остается прежним, так как он не зависит от того,
    # откуда были взяты утверждения - из кэша анализа или извлечены впервые.)
    if not isinstance(selected_claims_data, list) or not MAX_CLAIMS_TO_CHECK >= len(selected_claims_data) > 0:
        raise ValueError("Invalid selection of claims.")

    local_db = get_db_client()
    gemini_model = get_gemini_model()
    
    analysis_doc_ref = local_db.collection('analyses').document(analysis_id)
    analysis_doc = analysis_doc_ref.get()
    if not analysis_doc.exists:
        raise ValueError(f"Analysis ID {analysis_id} not found.")

    report_data = analysis_doc.to_dict()
    target_lang = report_data.get('target_lang', 'en')
    claims_ref = local_db.collection('claims')

    self.update_state(state='PROGRESS', meta={'status_message': f'Fact-checking {len(selected_claims_data)} statements...'})

    for claim_data in selected_claims_data:
        claim_hash = claim_data.get('hash')
        claim_text = claim_data.get('text')
        if not claim_hash or not claim_text: continue

        search_params = {'q': claim_text, 'key': GOOGLE_API_KEY, 'cx': SEARCH_ENGINE_ID, 'num': 4}
        search_response = requests.get("https://www.googleapis.com/customsearch/v1", params=search_params)
        search_results = search_response.json().get('items', [])
        
        search_context = " ".join([res.get('snippet', '') for res in search_results])
        sources = [res.get('link') for res in search_results]

        prompt_fc = f"""
        Based on the provided web search results, fact-check the following claim.
        Claim: "{claim_text}"
        Web Search Results Snippets: "{search_context}"
        Your task is to return a single JSON object with these keys: "verdict", "confidence_percentage", "explanation".
        - The "verdict" MUST be one of: "True", "False", "Misleading", "Partly True", "Unverifiable".
        - The "explanation" MUST be a concise, neutral summary, written STRICTLY in the following language: {target_lang}.
        - Your entire response must be ONLY a single JSON object.
        """
        fc_response = gemini_model.generate_content(prompt_fc)
        try:
            result_item = json.loads(re.search(r'\{.*\}', fc_response.text, re.DOTALL).group(0))
            result_item['sources'] = sources
            result_item['claim'] = claim_text
        except (AttributeError, json.JSONDecodeError):
            result_item = {"claim": claim_text, "verdict": "Unverifiable", "confidence_percentage": 0, "explanation": "AI failed to provide a valid analysis.", "sources": []}
        
        claim_to_cache = result_item.copy()
        claim_to_cache['last_checked_at'] = firestore.SERVER_TIMESTAMP
        claims_ref.document(claim_hash).set(claim_to_cache, merge=True)

    self.update_state(state='PROGRESS', meta={'status_message': 'Generating final report...'})

    all_claim_hashes = [item['hash'] for item in report_data.get('extracted_claims', [])]
    
    all_results = []
    # Firestore 'in' query limited to 30, so a loop is more robust
    docs = claims_ref.where(firestore.FieldPath.document_id(), 'in', all_claim_hashes).stream()
    all_results.extend([doc.to_dict() for doc in docs])
    
    verdict_counts = {"True": 0, "False": 0, "Misleading": 0, "Partly True": 0, "Unverifiable": 0}
    for res in all_results:
        verdict = res.get("verdict", "Unverifiable")
        if verdict in verdict_counts:
            verdict_counts[verdict] += 1
    
    confidence_sum = sum(res.get("confidence_percentage", 0) for res in all_results if isinstance(res.get("confidence_percentage"), int))
    claims_with_confidence = sum(1 for res in all_results if isinstance(res.get("confidence_percentage"), int) and res.get("confidence_percentage") > 0)
    average_confidence = round(confidence_sum / claims_with_confidence) if claims_with_confidence > 0 else 0

    true_verdicts = verdict_counts.get("True", 0)
    total_verdicts = len(all_results)
    confirmed_credibility = round((true_verdicts / total_verdicts) * 100) if total_verdicts > 0 else 0

    summary_context = [{"claim": res.get("claim"), "verdict": res.get("verdict")} for res in all_results]
    summary_prompt = f"""Analyze the fact-checking results... Data: {json.dumps(summary_context, ensure_ascii=False)}"""
    final_report_response = gemini_model.generate_content(summary_prompt)
    try:
        summary_data = json.loads(re.search(r'\{.*\}', final_report_response.text, re.DOTALL).group(0))
    except (AttributeError, json.JSONDecodeError):
        summary_data = {"overall_verdict": "Analysis Incomplete", "overall_assessment": "Could not generate a final summary.", "key_points": []}

    final_data_to_update = {
        "status": "COMPLETED",
        "summary_data": summary_data,
        "verdict_counts": verdict_counts,
        "average_confidence": average_confidence,
        "confirmed_credibility": confirmed_credibility,
        "detailed_results": all_results,
        "updated_at": firestore.SERVER_TIMESTAMP
    }
    
    analysis_doc_ref.update(final_data_to_update)

    data_to_return = report_data 
    data_to_return.update(final_data_to_update)
    data_to_return["id"] = analysis_doc_ref.id
    if 'created_at' in data_to_return and hasattr(data_to_return['created_at'], 'isoformat'):
        data_to_return['created_at'] = data_to_return['created_at'].isoformat()
    if 'updated_at' in data_to_return and hasattr(data_to_return['updated_at'], 'isoformat'):
        data_to_return['updated_at'] = data_to_return['updated_at'].isoformat()
    else:
        data_to_return['updated_at'] = datetime.now(timezone.utc).isoformat()
    
    return data_to_return