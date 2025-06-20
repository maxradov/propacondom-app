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
    # ... (содержимое функции без изменений) ...
    if not url: return None
    query = urlparse(url)
    if query.hostname in ('www.youtube.com', 'youtube.com'):
        if query.path == '/watch': return parse_qs(query.query).get('v', [None])[0]
        if query.path.startswith('/embed/'): return query.path.split('/')[2]
    if query.hostname == 'youtu.be': return query.path[1:]
    return None


@celery.task(bind=True, time_limit=600)
def fact_check_video(self, video_url, target_lang='en'):
    try:
        print(f"--- [LOG] Запуск задачи fact_check_video для URL: {video_url}, Язык: {target_lang} ---") # ← НОВЫЙ ЛОГ
        if not genai.api_key:
            raise ValueError("GEMINI_API_KEY не настроен. Проверьте переменные окружения.")

        video_id = get_video_id(video_url)
        if not video_id:
            raise ValueError("Некорректный URL видео YouTube. Убедитесь, что ссылка правильная.")
        print(f"[LOG] Извлечен video_id: {video_id}") # ← НОВЫЙ ЛОГ

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        doc = doc_ref.get()
        if doc.exists:
            # ... (код для кэша без изменений) ...
            return cached_data

        self.update_state(state='PROGRESS', meta={'status_message': 'Извлечение субтитров...'})
        print("[LOG] Шаг 1: Получение списка доступных субтитров через yt-dlp...") # ← НОВЫЙ ЛОГ
        
        ydl_opts_list = {'listsubtitles': True, 'quiet': True}
        info = yt_dlp.YoutubeDL(ydl_opts_list).extract_info(video_url, download=False)
        print("[LOG] Информация о видео получена.") # ← НОВЫЙ ЛОГ
        
        if not info:
            raise ValueError("Не удалось получить информацию о видео. Возможно, оно недоступно.")

        available_subs = info.get('automatic_captions', {}) or info.get('subtitles', {})
        if not available_subs:
            raise ValueError("Для этого видео не найдено никаких субтитров.")
        print(f"[LOG] Найдены субтитры для языков: {list(available_subs.keys())}") # ← НОВЫЙ ЛОГ

        priority_langs = [target_lang, 'en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_subs), list(available_subs.keys())[0])
        print(f"[LOG] Шаг 2: Выбран язык для загрузки субтитров: {detected_lang}") # ← НОВЫЙ ЛОГ

        subtitle_filename = f'subtitles_{video_id}.{detected_lang}.vtt'
        ydl_opts_download = {'writeautomaticsub': True, 'subtitleslangs': [detected_lang], 'skip_download': True, 'outtmpl': f'subtitles_{video_id}'}
        
        print(f"[LOG] Шаг 3: Загрузка файла субтитров ({subtitle_filename})...") # ← НОВЫЙ ЛОГ
        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl: ydl.download([video_url])
        print("[LOG] Файл субтитров загружен.") # ← НОВЫЙ ЛОГ
        
        with open(subtitle_filename, 'r', encoding='utf-8') as f: content = f.read()
        lines = [re.sub(r'<[^>]+>', '', l).strip() for l in content.splitlines() if '-->' not in l and 'WEBVTT' not in l and l.strip() != '']
        clean_text = " ".join(dict.fromkeys(lines))
        os.remove(subtitle_filename)
        print("[LOG] Субтитры очищены и готовы к анализу.") # ← НОВЫЙ ЛОГ

        self.update_state(state='PROGRESS', meta={'status_message': 'Субтитры извлечены. Анализ текста...'})
        model = genai.GenerativeModel('gemini-1.5-flash')
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}

        prompt_claims = f"Analyze the following transcript in '{detected_lang}'. Extract up to 10 main factual claims as a numbered list. Transcript: --- {clean_text} ---"
        print("[LOG] Шаг 4: Отправка транскрипта в Gemini для извлечения утверждений...") # ← НОВЫЙ ЛОГ
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line) for line in response_claims.text.strip().split('\n')]
        claims_to_check = claims_list[:10]
        total_claims = len(claims_to_check)
        print(f"[LOG] Gemini вернул {len(claims_list)} утверждений. Взято в работу: {total_claims}.") # ← НОВЫЙ ЛОГ

        self.update_state(state='PROGRESS', meta={'status_message': f'Извлечено {total_claims} утверждений. Начинаю факт-чекинг...'})
        
        all_results = []
        for i, claim in enumerate(claims_to_check):
            current_claim_num = i + 1
            self.update_state(state='PROGRESS', meta={'status_message': f'Проверка утверждения {current_claim_num} из {total_claims}...'})
            print(f"[LOG] --> Факт-чекинг утверждения {current_claim_num}: '{claim[:50]}...'") # ← НОВЫЙ ЛОГ
            
            # ... (остальная логика факт-чекинга без изменений) ...

        self.update_state(state='PROGRESS', meta={'status_message': 'Все утверждения проверены. Формирование итогового отчета...'})
        print("[LOG] Шаг 5: Отправка результатов в Gemini для генерации итогового отчета...") # ← НОВЫЙ ЛОГ
        # ... (код генерации итогового отчета) ...
        print("[LOG] Итоговый отчет получен.") # ← НОВЫЙ ЛОГ
        
        # ... (код сохранения в Firestore и возврата результата) ...
        print(f"--- [LOG] Задача fact_check_video успешно завершена. ---") # ← НОВЫЙ ЛОГ
        return data_to_return

    except Exception as e:
        print(f"!!! [LOG] Произошла критическая ошибка в задаче: {e}") # ← НОВЫЙ ЛОГ
        self.update_state(state='FAILURE', meta={'status_message': f'Произошла ошибка: {str(e)}'})
        return {"error": str(e)}