# AIzaSyDm2gBtIMh0UhhpDytCRL-xG5AB7RjWx1Q <-- НЕ ЗАБУДЬТЕ ВСТАВИТЬ КЛЮЧ

import os
import re
import json
import datetime
import yt_dlp
import google.generativeai as genai
from celery import Celery
from google.cloud import firestore
from urllib.parse import urlparse, parse_qs # <-- НОВЫЙ ИМПОРТ

# --- Настройки ---
celery = Celery(__name__, broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', "AIzaSyDm2gBtIMh0UhhpDytCRL-xG5AB7RjWx1Q")
genai.configure(api_key=GEMINI_API_KEY)
db = firestore.Client(project='propacondom')

# ↓↓↓ НОВАЯ, НАДЕЖНАЯ ФУНКЦИЯ ↓↓↓
def get_video_id(url):
    """Надежно извлекает ID видео из любого варианта URL YouTube."""
    if not url:
        return None
    query = urlparse(url)
    if query.hostname == 'youtu.be':
        return query.path[1:]
    if query.hostname in ('www.youtube.com', 'youtube.com'):
        if query.path == '/watch':
            p = parse_qs(query.query)
            return p.get('v', [None])[0]
        if query.path.startswith(('/embed/', '/v/')):
            return query.path.split('/')[2]
    return None

@celery.task(time_limit=600)
def fact_check_video(video_url, target_lang='en'):
    try:
        video_id = get_video_id(video_url)
        if not video_id:
            raise ValueError("Некорректный URL видео YouTube. Убедитесь, что ссылка правильная.")

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        doc = doc_ref.get()
        if doc.exists:
            print(f"Найден кэш для видео {video_id} на языке {target_lang}.")
            return doc.to_dict()

        # ... (остальной код задачи остается без изменений) ...
        # Он будет вставлен сюда автоматически, когда вы скопируете весь файл.
        # Просто скопируйте весь блок кода из этого ответа целиком.
        
        print(f"Кэш не найден. Запускаю полный анализ для {video_id}.")
        
        ydl_opts_list = {'listsubtitles': True, 'quiet': True}
        info = yt_dlp.YoutubeDL(ydl_opts_list).extract_info(video_url, download=False)
        
        if not info:
            raise ValueError("Не удалось получить информацию о видео. Возможно, оно недоступно.")

        available_subs = info.get('automatic_captions', {}) or info.get('subtitles', {})
        if not available_subs:
            raise ValueError("Для этого видео не найдено никаких субтитров.")

        priority_langs = ['en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_subs), list(available_subs.keys())[0])

        subtitle_filename = f'subtitles_{video_id}.{detected_lang}.vtt'
        ydl_opts_download = {'writeautomaticsub': True, 'subtitleslangs': [detected_lang], 'skip_download': True, 'outtmpl': f'subtitles_{video_id}'}
        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl: ydl.download([video_url])
        
        with open(subtitle_filename, 'r', encoding='utf-8') as f: content = f.read()
        lines = [re.sub(r'<[^>]+>', '', l).strip() for l in content.splitlines() if '-->' not in l and 'WEBVTT' not in l and l.strip() != '']
        clean_text = " ".join(dict.fromkeys(lines))
        os.remove(subtitle_filename)

        model = genai.GenerativeModel('gemini-1.5-flash')
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}

        prompt_claims = f"Analyze the following transcript in '{detected_lang}'. Extract 5-10 main factual claims as a numbered list. Transcript: --- {clean_text} ---"
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line) for line in response_claims.text.strip().split('\n')]
        
        all_results = []
        for claim in claims_list:
            if not claim: continue
            prompt_fc = f'Fact-check this claim: "{claim}". Return ONLY a JSON object with keys: "claim", "verdict" (True, False, Partly True/Manipulation, No data), "confidence_percentage" (0-100), "explanation" (in RUSSIAN), "sources" (array of URLs).'
            response_fc = model.generate_content(prompt_fc, safety_settings=safety_settings)
            json_match = re.search(r'\{.*\}', response_fc.text, re.DOTALL)
            if json_match:
                all_results.append(json.loads(json_match.group(0)))
            else:
                all_results.append({"claim": claim, "verdict": "Processing Error", "explanation": "AI did not return valid JSON."})
        
        verdict_counts = {"true": 0, "false": 0, "manipulation": 0, "nodata": 0}
        for res in all_results:
            verdict = res.get("verdict", "No data")
            if verdict == "True": verdict_counts['true'] += 1
            elif verdict == "False": verdict_counts['false'] += 1
            elif "Partly True" in res.get("verdict", ""): verdict_counts['manipulation'] += 1
            else: verdict_counts['nodata'] += 1

        summary_prompt = f"Act as an editor-in-chief for a fact-checking agency. Based on the provided JSON data, write a final summary report in the language with code: '{target_lang}'. Report structure: 1. Overall Verdict. 2. Overall Assessment (2-3 sentences). 3. Key Points. Data: {json.dumps(all_results, ensure_ascii=False)}"
        final_report_response = model.generate_content(summary_prompt, safety_settings=safety_settings)

        data_to_save_in_db = {
            "summary_html": final_report_response.text,
            "verdict_counts": verdict_counts,
            "detailed_results": all_results,
            "created_at": firestore.SERVER_TIMESTAMP 
        }
        
        print(f"Сохраняю отчет для {video_id} ({target_lang}) в Firestore...")
        doc_ref.set(data_to_save_in_db)

        # 2. Готовим объект для возврата в Celery/Redis, заменяя метку на обычную строку
        data_to_return = data_to_save_in_db.copy()
        data_to_return["created_at"] = datetime.datetime.now().isoformat()
        
        return data_to_return

    except Exception as e:
        print(f"!!! Произошла критическая ошибка в задаче: {e}")
        return {"error": str(e)}