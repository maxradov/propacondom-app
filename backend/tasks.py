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


# Предполагается, что эти константы определены в файле constants.py
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
    """Возвращает стабильный sha256-хеш для уникальной идентификации утверждения."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def get_text_hash(text):
    """Возвращает стабильный 16-символьный sha1-хеш для общего текста."""
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


# --- Основные задачи Celery ---

@celery.task(bind=True, name='tasks.extract_claims', time_limit=300)
def extract_claims(self, user_input, target_lang='en'):
    """
    Главная точка входа. Определяет тип ввода и запускает соответствующий анализатор.
    Это ЗАДАЧА №1 (Извлечение утверждений).
    """
    if is_youtube_url(user_input):
        return analyze_youtube_video(self, user_input, target_lang)
    elif is_url(user_input):
        return analyze_web_url(self, user_input, target_lang)
    else:
        # Для простого текста ID анализа будет создан внутри analyze_free_text
        return analyze_free_text(self, user_input, target_lang, user_text=user_input, input_type="text")


def analyze_youtube_video(self, video_url, target_lang='en'):
    """Получает и обрабатывает данные с YouTube."""
    if not all([SEARCHAPI_KEY, GOOGLE_API_KEY, SEARCH_ENGINE_ID]):
        raise ValueError("One or more API keys are not configured.")

    video_id = get_video_id(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    # ID анализа для YouTube всегда уникален для видео и языка
    analysis_id = f"{video_id}_{target_lang}"

    self.update_state(state='PROGRESS', meta={'status_message': 'Fetching video details...'})
    params_details = {'engine': 'youtube_video', 'video_id': video_id, 'api_key': SEARCHAPI_KEY}
    details_response = requests.get('https://www.searchapi.io/api/v1/search', params=params_details)
    details_response.raise_for_status()
    details_data = details_response.json().get('video', {})
    video_title = details_data.get('title', 'Title Not Found')
    thumbnail_url = details_data.get('thumbnail', '')

    self.update_state(state='PROGRESS', meta={'status_message': 'Extracting subtitles...'})
    params_get_transcript = {'engine': 'youtube_transcripts', 'video_id': video_id, 'lang': target_lang, 'api_key': SEARCHAPI_KEY}
    transcript_data = requests.get('https://www.searchapi.io/api/v1/search', params=params_get_transcript).json()
    if not transcript_data.get('transcripts'):
        raise ValueError(f"API did not return subtitles for '{target_lang}'.")
    clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])

    return analyze_free_text(self, clean_text, target_lang, title=video_title, thumbnail_url=thumbnail_url,
                             source_url=video_url, analysis_id=analysis_id, input_type="youtube")


def analyze_web_url(self, url, target_lang='en'):
    """Получает и обрабатывает данные с веб-страницы."""
    import bs4
    try:
        self.update_state(state='PROGRESS', meta={'status_message': 'Downloading web page...'})
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        soup = bs4.BeautifulSoup(response.text, "html.parser")
        
        for tag in soup(['script', 'style', 'noscript', 'header', 'footer', 'nav', 'aside']):
            tag.decompose()
            
        text = soup.get_text(separator=' ', strip=True)
        if not text or len(text) < 200: raise ValueError("No readable content found.")
        
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        # ID анализа для URL основан на хеше содержимого
        analysis_id = f"url_{get_text_hash(text)}_{target_lang}"
        
        return analyze_free_text(self, text[:15000], target_lang, title=title, source_url=url,
                                 analysis_id=analysis_id, input_type="url")
    except Exception as e:
        raise ValueError(f"Could not retrieve or parse the site: {str(e)}")


def analyze_free_text(self, text, target_lang='en', title=None, thumbnail_url=None, source_url=None, analysis_id=None, input_type="text", user_text=None):
    """
    Ядро первого этапа: извлекает утверждения, проверяет кэш для каждого
    и сохраняет промежуточный результат.
    """
    local_db = get_db_client()
    gemini_model = get_gemini_model()

    # Если ID не был создан ранее (для случая с простым текстом)
    if not analysis_id:
        analysis_id = f"text_{get_text_hash(text)}_{target_lang}"

    self.update_state(state='PROGRESS', meta={'status_message': 'AI is extracting claims...'})
    prompt_claims = f"""Analyze the following text. Extract up to {MAX_CLAIMS_EXTRACTED} main factual claims that can be verified.
Focus on specific, checkable statements. Present them as a numbered list.
IMPORTANT: The claims must be written in {target_lang}.
Text: --- {text[:15000]} ---"""

    response_claims = gemini_model.generate_content(prompt_claims)
    claims_list_text = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip()]
    if not claims_list_text:
        raise ValueError("AI was unable to extract any claims from the provided content.")
        
    self.update_state(state='PROGRESS', meta={'status_message': f'Extracted {len(claims_list_text)} statements. Checking cache...'})

    # --- Новая логика кэширования на уровне утверждений ---
    claims_ref = local_db.collection('claims')
    cache_expiry_date = datetime.now(timezone.utc) - timedelta(days=CACHE_EXPIRATION_DAYS)
    
    claims_for_frontend = []
    claims_for_db = []

    for claim_text in claims_list_text:
        claim_hash = get_claim_hash(claim_text)
        claim_doc_ref = claims_ref.document(claim_hash)
        claim_doc = claim_doc_ref.get()
        
        claim_data_for_db = {"hash": claim_hash, "text": claim_text}
        claims_for_db.append(claim_data_for_db)
        
        if claim_doc.exists:
            cached_data = claim_doc.to_dict()
            last_checked = cached_data.get('last_checked_at')
            if last_checked and last_checked.replace(tzinfo=timezone.utc) > cache_expiry_date:
                # Кэш валиден
                claims_for_frontend.append({
                    "hash": claim_hash,
                    "text": claim_text,
                    "is_cached": True,
                    "cached_data": cached_data
                })
                continue
        
        # Кэш не найден или устарел
        claims_for_frontend.append({"hash": claim_hash, "text": claim_text, "is_cached": False})

    # Сохраняем документ в коллекцию 'analyses'
    analysis_doc_ref = local_db.collection('analyses').document(analysis_id)
    analysis_data = {
        "status": "PENDING_SELECTION",
        "input_type": input_type,
        "source_url": source_url or "",
        "video_title": title or "Text Analysis",
        "thumbnail_url": thumbnail_url or "",
        "extracted_claims": claims_for_db, # В БД храним текст и хеш
        "target_lang": target_lang,
        "created_at": firestore.SERVER_TIMESTAMP
    }
    if input_type == "text":
        analysis_data["user_text"] = user_text

    analysis_doc_ref.set(analysis_data)
    
    # Возвращаем на фронтенд полные данные для рендеринга
    return {
        "id": analysis_id,
        "claims_for_selection": claims_for_frontend
    }

@celery.task(bind=True, name='tasks.fact_check_selected', time_limit=600)
def fact_check_selected_claims(self, analysis_id, selected_claims_data):
    """
    Ядро второго этапа: получает выбранные пользователем утверждения,
    проверяет их и формирует итоговый отчет.
    """
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

    # --- 1. Проверяем только выбранные НОВЫЕ утверждения ---
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
            result_item['sources'] = sources # Добавляем источники
            result_item['claim'] = claim_text # Добавляем текст утверждения
        except (AttributeError, json.JSONDecodeError):
            result_item = {"claim": claim_text, "verdict": "Unverifiable", "confidence_percentage": 0, "explanation": "AI failed to provide a valid analysis.", "sources": []}
        
        # Сохраняем результат в коллекцию 'claims' для кэширования
        claim_to_cache = result_item.copy()
        claim_to_cache['last_checked_at'] = firestore.SERVER_TIMESTAMP
        claims_ref.document(claim_hash).set(claim_to_cache, merge=True)

    self.update_state(state='PROGRESS', meta={'status_message': 'Generating final report...'})

    # --- 2. Собираем ВСЕ утверждения (новые и кэшированные) для финального отчета ---
    all_claim_hashes = [item['hash'] for item in report_data.get('extracted_claims', [])]
    
    # Firestore 'in' query limited to 30, so we may need to batch for larger lists
    all_results = []
    for i in range(0, len(all_claim_hashes), 30):
        batch_hashes = all_claim_hashes[i:i+30]
        for claim_hash in batch_hashes:
            doc = claims_ref.document(claim_hash).get()
            if doc.exists:
                all_results.append(doc.to_dict())
    
    # --- 3. Генерируем финальное саммари и статистику (код без изменений) ---
    verdict_counts = {"True": 0, "False": 0, "Misleading": 0, "Partly True": 0, "Unverifiable": 0}
    # ... (остальной код для подсчета статистики)
    confidence_sum = sum(res.get("confidence_percentage", 0) for res in all_results if isinstance(res.get("confidence_percentage"), int))
    claims_with_confidence = sum(1 for res in all_results if isinstance(res.get("confidence_percentage"), int) and res.get("confidence_percentage") > 0)
    average_confidence = round(confidence_sum / claims_with_confidence) if claims_with_confidence > 0 else 0
    for res in all_results:
        verdict = res.get("verdict", "Unverifiable")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
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
    try:
        summary_data = json.loads(re.search(r'\{.*\}', final_report_response.text, re.DOTALL).group(0))
    except (AttributeError, json.JSONDecodeError):
        summary_data = {"overall_verdict": "Analysis Incomplete", "overall_assessment": "Could not generate a final summary.", "key_points": []}

    # --- 4. Обновляем и возвращаем итоговый документ ---
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
    # Ensure updated_at is a string for JSON serialization
    data_to_return["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    return data_to_return