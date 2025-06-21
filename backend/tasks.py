import os
import re
import json
import datetime
import requests # <-- Используем новую библиотеку
import google.generativeai as genai
from celery import Celery
from google.cloud import firestore
from urllib.parse import urlparse, parse_qs

# --- Настройки ---
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
celery = Celery(__name__, broker=REDIS_URL, backend=REDIS_URL)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
SEARCHAPI_KEY = os.environ.get('SEARCHAPI_KEY') # <-- Получаем новый ключ

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

db = firestore.Client()

def get_video_id(url):
    """Надежно извлекает ID видео из любого варианта URL YouTube."""
    if not url: return None
    regex = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None

@celery.task(bind=True, time_limit=600)
def fact_check_video(self, video_url, target_lang='en'):
    try:
        video_id = get_video_id(video_url)
        if not video_id: raise ValueError(f"Не удалось извлечь ID видео из URL: {video_url}")

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        doc = doc_ref.get()
        if doc.exists:
            print(f"[LOG-SUCCESS] Найден и возвращен кэш из Firestore.")
            cached_data = doc.to_dict()
            if 'created_at' in cached_data and hasattr(cached_data['created_at'], 'isoformat'):
                 cached_data['created_at'] = cached_data['created_at'].isoformat()
            return cached_data

        # --- НАЧАЛО ИЗМЕНЕНИЙ В ЭТОМ БЛОКЕ ---
        print(f"[LOG-STEP 2] Запрос метаданных и субтитров через SearchApi.io...")
        self.update_state(state='PROGRESS', meta={'status_message': 'Проверка доступных языков...'})

        api_key = os.environ.get('SEARCHAPI_KEY')
        # Сразу запрашиваем и транскрипт, и метаданные
        params = {
            'engine': 'youtube_transcripts', 'video_id': video_id, 'api_key': api_key
        }
        response = requests.get('https://www.searchapi.io/api/v1/search', params=params)
        response.raise_for_status()
        metadata = response.json()

        if 'error' in metadata and 'available_languages' not in metadata:
             raise Exception(f"SearchApi.io вернул ошибку: {metadata['error']}")

        # Извлекаем название видео и превью из метаданных
        video_title = metadata.get('video_details', {}).get('title', 'Video Title Not Found')
        thumbnail_url = metadata.get('video_details', {}).get('thumbnail', '')
        
        available_langs = [lang.get('lang') for lang in metadata.get('available_languages', []) if lang.get('lang')]
        if not available_langs: raise ValueError("API не нашел доступных субтитров ни на одном языке.")
        
        priority_langs = [target_lang, 'en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_langs), available_langs[0])
        
        self.update_state(state='PROGRESS', meta={'status_message': f'Извлечение субтитров ({detected_lang})...'})
        
        # Если транскрипт для нужного языка не пришел с первым запросом, делаем второй
        if not metadata.get('transcripts'):
            params['lang'] = detected_lang
            response_transcript = requests.get('https://www.searchapi.io/api/v1/search', params=params)
            response_transcript.raise_for_status()
            transcript_data = response_transcript.json()
        else:
            transcript_data = metadata

        if 'error' in transcript_data: raise Exception(f"SearchApi.io вернул ошибку: {transcript_data['error']}")
        if not transcript_data.get('transcripts'): raise ValueError(f"API не вернул субтитры для языка '{detected_lang}'.")

        clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])
        # --- КОНЕЦ ИЗМЕНЕНИЙ В ЭТОМ БЛОКЕ ---

        # ... (остальной код с вызовами Gemini и подсчетом статистики остается без изменений) ...
        # ... ... ...

        # В самом конце, перед сохранением в БД, обновляем структуру данных
        data_to_save_in_db = {
            "video_url": video_url, # <-- Добавлено
            "video_title": video_title, # <-- Добавлено
            "thumbnail_url": thumbnail_url, # <-- Добавлено
            "summary_data": summary_data,
            "verdict_counts": verdict_counts,
            "average_confidence": average_confidence,
            "detailed_results": all_results,
            "created_at": firestore.SERVER_TIMESTAMP 
        }
        
        doc_ref.set(data_to_save_in_db)
        data_to_return = data_to_save_in_db.copy()
        data_to_return["created_at"] = datetime.datetime.now().isoformat()
        
        return data_to_return

    except Exception as e:
        print(f"!!! [LOG-CRITICAL] Произошла критическая ошибка в задаче: {e}")
        raise e