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

db = firestore.Client()

def get_video_id(url):
    """Надежно извлекает ID видео из любого варианта URL YouTube."""
    if not url:
        return None
    # Простое извлечение ID для стандартных ссылок
    regex = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    if match:
        return match.group(1)
    return None

@celery.task(bind=True, time_limit=600)
def fact_check_video(self, video_url, target_lang='en'):
    try:
        video_id = get_video_id(video_url)
        if not video_id:
            raise ValueError(f"Не удалось извлечь ID видео из URL: {video_url}")

        print(f"[LOG-START] Запущена задача для video_id: {video_id}, язык: {target_lang}")

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        print(f"[LOG-STEP 1] Проверка кэша в Firestore...")
        doc = doc_ref.get()
        if doc.exists:
            print(f"[LOG-SUCCESS] Найден и возвращен кэш из Firestore.")
            cached_data = doc.to_dict()
            if 'created_at' in cached_data and hasattr(cached_data['created_at'], 'isoformat'):
                 cached_data['created_at'] = cached_data['created_at'].isoformat()
            return cached_data

        print(f"[LOG-INFO] Кэш не найден. Начинается полный анализ.")
        self.update_state(state='PROGRESS', meta={'status_message': 'Извлечение субтитров...'})

        info = None
        try:
            print(f"[LOG-STEP 2] Вызов yt-dlp для получения информации о видео и списка субтитров...")
            ydl_opts_list = {
                'listsubtitles': True,
                'quiet': True,
                'nocheckcertificate': True, # <--- Вот здесь ставится запятая
                'cookiefile': '/app/cookies.txt' # <--- А вот новая опция
            }
            info = yt_dlp.YoutubeDL(ydl_opts_list).extract_info(video_url, download=False)
            print(f"[LOG-SUCCESS] yt-dlp успешно получил информацию о видео.")
        except Exception as ytdl_error:
            print(f"!!! [LOG-CRITICAL] ОШИБКА на шаге 2 при вызове yt-dlp: {ytdl_error}")
            raise ytdl_error

        if not info: raise ValueError("yt-dlp не смог получить информацию о видео.")
        
        available_subs = info.get('automatic_captions', {}) or info.get('subtitles', {})
        if not available_subs: raise ValueError("Для этого видео не найдено никаких субтитров.")
        print(f"[LOG-INFO] Найдены субтитры для языков: {list(available_subs.keys())}")

        priority_langs = [target_lang, 'en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_subs), list(available_subs.keys())[0])

        print(f"[LOG-STEP 3] Загрузка файла субтитров для языка: {detected_lang}...")
        subtitle_filename = f'subtitles_{video_id}.{detected_lang}.vtt'
        ydl_opts_download = {'writeautomaticsub': True, 'subtitleslangs': [detected_lang], 'skip_download': True, 'outtmpl': f'subtitles_{video_id}'}
        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl: ydl.download([video_url])
        print(f"[LOG-SUCCESS] Файл субтитров загружен. Идет очистка...")
        
        with open(subtitle_filename, 'r', encoding='utf-8') as f: content = f.read()
        lines = [re.sub(r'<[^>]+>', '', l).strip() for l in content.splitlines() if '-->' not in l and 'WEBVTT' not in l and l.strip() != '']
        clean_text = " ".join(dict.fromkeys(lines))
        os.remove(subtitle_filename)
        print(f"[LOG-SUCCESS] Субтитры очищены и готовы к анализу.")

        self.update_state(state='PROGRESS', meta={'status_message': 'Субтитры извлечены. Анализ текста...'})
        model = genai.GenerativeModel('gemini-1.5-flash')
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}

        print(f"[LOG-STEP 4] Вызов Gemini для извлечения утверждений...")
        prompt_claims = f"Analyze the following transcript in '{detected_lang}'. Extract up to 10 main factual claims as a numbered list. Transcript: --- {clean_text} ---"
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line) for line in response_claims.text.strip().split('\n')]
        claims_to_check = claims_list[:10]
        total_claims = len(claims_to_check)
        print(f"[LOG-SUCCESS] Gemini вернул {len(claims_list)} утверждений. Взято в работу: {total_claims}.")

        self.update_state(state='PROGRESS', meta={'status_message': f'Извлечено {total_claims} утверждений. Начинаю факт-чекинг...'})
        
        all_results = []
        print(f"[LOG-STEP 5] Старт цикла факт-чекинга...")
        for i, claim in enumerate(claims_to_check):
            current_claim_num = i + 1
            print(f"[LOG-INFO] Проверка утверждения {current_claim_num} из {total_claims}...")
            if not claim: continue
            prompt_fc = f'Fact-check this claim: "{claim}". Return ONLY a JSON object with keys: "claim", "verdict" (True, False, Partly True/Manipulation, No data), "confidence_percentage" (0-100), "explanation" (in {target_lang}), "sources" (array of URLs).'
            response_fc = model.generate_content(prompt_fc, safety_settings=safety_settings)
            json_match = re.search(r'\{.*\}', response_fc.text, re.DOTALL)
            if json_match:
                all_results.append(json.loads(json_match.group(0)))
            else:
                all_results.append({"claim": claim, "verdict": "Processing Error", "explanation": "AI did not return valid JSON."})
        print(f"[LOG-SUCCESS] Цикл факт-чекинга завершен.")
        
        self.update_state(state='PROGRESS', meta={'status_message': 'Все утверждения проверены. Формирование итогового отчета...'})
        
        # Мы убрали подсчет вердиктов, так как он не использовался. Если понадобится, можно вернуть.
        # verdict_counts = {"True": 0, "False": 0, "Partly True/Manipulation": 0, "No data": 0, "Processing Error": 0}

        print(f"[LOG-STEP 6] Вызов Gemini для генерации итогового отчета...")
        summary_prompt = f"Act as an editor-in-chief for a fact-checking agency. Based on the provided JSON data, write a final summary report in the language with code: '{target_lang}'. Report structure: 1. Overall Verdict. 2. Overall Assessment (2-3 sentences). 3. Key Points. Data: {json.dumps(all_results, ensure_ascii=False)}"
        final_report_response = model.generate_content(summary_prompt, safety_settings=safety_settings)
        print(f"[LOG-SUCCESS] Итоговый отчет получен.")
        
        data_to_save_in_db = {
            "summary_html": final_report_response.text,
            "verdict_counts": {}, # Заглушка
            "detailed_results": all_results,
            "created_at": firestore.SERVER_TIMESTAMP 
        }
        
        print(f"[LOG-STEP 7] Сохранение отчета в Firestore...")
        doc_ref.set(data_to_save_in_db)
        print(f"[LOG-SUCCESS] Отчет сохранен в Firestore.")

        data_to_return = data_to_save_in_db.copy()
        data_to_return["created_at"] = datetime.datetime.now().isoformat()
        
        print(f"--- [LOG-END] Задача успешно завершена. ---")
        return data_to_return

    except Exception as e:
        print(f"!!! [LOG-CRITICAL] Произошла критическая ошибка в задаче: {e}")
        # Передаем исключение дальше, чтобы Celery сам его обработал.
        # Это надежнее и должно предотвратить 'KeyError' в app.py
        raise e