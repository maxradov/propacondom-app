import os
import re
import json
import datetime
import yt_dlp
import google.generativeai as genai
from celery import Celery
from google.cloud import firestore
from urllib.parse import urlparse, parse_qs

# --- Настройки ---
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
celery = Celery(__name__, broker=REDIS_URL, backend=REDIS_URL)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

db = firestore.Client(project='propacondom')

def get_video_id(url):
    """Надежно извлекает ID видео из любого варианта URL YouTube."""
    if not url: return None
    query = urlparse(url)
    if query.hostname in ('www.youtube.com', 'youtube.com'):
        if query.path == '/watch': return parse_qs(query.query).get('v', [None])[0]
        if query.path.startswith('/embed/'): return query.path.split('/')[2]
    if query.hostname == 'youtu.be': return query.path[1:]
    return None

# ↓↓↓ ИЗМЕНЕНИЕ 1: Добавляем bind=True и self в аргументы ↓↓↓
@celery.task(bind=True, time_limit=600)
def fact_check_video(self, video_url, target_lang='en'):
    try:
        # ... (проверки ключа, video_id, кэша остаются без изменений) ...

        # ↓↓↓ ИЗМЕНЕНИЕ 2: Добавляем вызовы update_state ↓↓↓
        self.update_state(state='PROGRESS', meta={'status_message': 'Извлечение субтитров...'})
        
        # ... (код для получения субтитров) ...

        self.update_state(state='PROGRESS', meta={'status_message': 'Субтитры извлечены. Анализ текста и извлечение утверждений...'})
        
        # ... (код для очистки текста и запроса к Gemini для извлечения утверждений) ...

        claims_list = [re.sub(r'^\d+\.\s*', '', line) for line in response_claims.text.strip().split('\n')]
        claims_to_check = claims_list[:10]
        total_claims = len(claims_to_check)

        self.update_state(state='PROGRESS', meta={'status_message': f'Извлечено {total_claims} утверждений. Начинаю факт-чекинг...'})
        
        all_results = []
        for i, claim in enumerate(claims_to_check):
            current_claim_num = i + 1
            self.update_state(state='PROGRESS', meta={'status_message': f'Проверка утверждения {current_claim_num} из {total_claims}...'})
            
            if not claim: continue
            # ... (код для факт-чекинга каждого утверждения) ...
        
        self.update_state(state='PROGRESS', meta={'status_message': 'Все утверждения проверены. Формирование итогового отчета...'})

        # ... (код для подсчета вердиктов и генерации итогового отчета) ...

        return data_to_return

    except Exception as e:
        self.update_state(state='FAILURE', meta={'status_message': f'Произошла ошибка: {str(e)}'})
        print(f"!!! Произошла критическая ошибка в задаче: {e}")
        return {"error": str(e)}