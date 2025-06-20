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

        self.update_state(state='PROGRESS', meta={'status_message': 'Проверка доступных языков...'})
        api_key = os.environ.get('SEARCHAPI_KEY')
        params_list_langs = {'engine': 'youtube_transcripts', 'video_id': video_id, 'api_key': api_key}
        response_list_langs = requests.get('https://www.searchapi.io/api/v1/search', params=params_list_langs)
        response_list_langs.raise_for_status()
        metadata = response_list_langs.json()

        if 'error' in metadata and 'available_languages' not in metadata:
             raise Exception(f"SearchApi.io вернул ошибку: {metadata['error']}")

        available_langs = [lang.get('lang') for lang in metadata.get('available_languages', []) if lang.get('lang')]
        if not available_langs: raise ValueError("API не нашел доступных субтитров ни на одном языке.")
        
        priority_langs = [target_lang, 'en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_langs), available_langs[0])
        
        self.update_state(state='PROGRESS', meta={'status_message': f'Извлечение субтитров ({detected_lang})...'})
        
        params_get_transcript = {'engine': 'youtube_transcripts', 'video_id': video_id, 'lang': detected_lang, 'api_key': api_key}
        response_transcript = requests.get('https://www.searchapi.io/api/v1/search', params=params_get_transcript)
        response_transcript.raise_for_status()
        transcript_data = response_transcript.json()
        
        if 'error' in transcript_data: raise Exception(f"SearchApi.io вернул ошибку: {transcript_data['error']}")
        if not transcript_data.get('transcripts'): raise ValueError(f"API не вернул субтитры для языка '{detected_lang}'.")

        clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])
        
        self.update_state(state='PROGRESS', meta={'status_message': 'Анализ текста...'})
        model = genai.GenerativeModel('gemini-1.5-flash')
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}

        prompt_claims = f"Analyze the following transcript. Extract the 5 most important and significant factual claims. Return them as a numbered list. Transcript: --- {clean_text} ---"
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip()]

        self.update_state(state='PROGRESS', meta={'status_message': f'Извлечено {len(claims_list)} утверждений. Начинаю факт-чекинг...'})
        
        claims_json_string = json.dumps(claims_list, ensure_ascii=False)
        
        # --- НАЧАЛО ИЗМЕНЕНИЙ ---
        
        # 1. Улучшенный промпт
        prompt_fc_batch = f"""
        Fact-check each claim in the following JSON array of strings.
        Your response MUST be ONLY a single valid JSON array of objects. Do not include any text before or after the array.
        Each object in the array must correspond to a claim and have these exact keys: "claim", "verdict" (one of: "True", "False", "Partly True/Manipulation", "No data"), "confidence_percentage" (integer 0-100), "explanation" (a string in {target_lang}), "sources" (an array of URL strings).
        Wrap your entire response in ```json ... ```.

        Claims to check: {claims_json_string}
        """
        
        response_fc_batch = model.generate_content(prompt_fc_batch, safety_settings=safety_settings)
        
        # 2. Улучшенный, "умный" парсинг
        response_text = response_fc_batch.text
        print(f"[LOG-DEBUG] Raw fact-check response from AI: {response_text}")

        json_str = None
        # Сначала ищем JSON внутри markdown-блока ```json ... ```
        match = re.search(r'```json\s*(\[.*\])\s*```', response_text, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            # Если не нашли, ищем первый же массив в тексте
            match = re.search(r'\[.*\]', response_text, re.DOTALL)
            if match:
                json_str = match.group(0)

        if json_str:
            all_results = json.loads(json_str)
        else:
            raise ValueError("AI did not return a parsable JSON array for the fact-check.")
        
        # --- КОНЕЦ ИЗМЕНЕНИЙ ---
            
        print(f"[LOG-SUCCESS] Пакетный факт-чекинг завершен.")
        
        self.update_state(state='PROGRESS', meta={'status_message': 'Формирование итогового отчета...'})

        verdict_counts = {"True": 0, "False": 0, "Unverifiable": 0}
        confidence_sum = 0
        claims_with_confidence = 0
        for res in all_results:
            verdict = res.get("verdict", "Unverifiable")
            if verdict == "True": verdict_counts["True"] += 1
            elif verdict == "False": verdict_counts["False"] += 1
            else: verdict_counts["Unverifiable"] += 1
            if "confidence_percentage" in res:
                confidence_sum += int(res["confidence_percentage"])
                claims_with_confidence += 1
        average_confidence = round(confidence_sum / claims_with_confidence) if claims_with_confidence > 0 else 0
        
        summary_prompt = f"""
        Analyze the provided list of fact-checked claims. Return ONLY a single JSON object with the following keys: "overall_verdict" (one of: "Mostly True", "Mixed", "Mostly False", "Misleading"), "overall_assessment" (a 2-3 sentence summary in {target_lang}), and "key_points" (a list of 2-3 key takeaway strings in {target_lang}).
        Data: {json.dumps(all_results, ensure_ascii=False)}
        """
        final_report_response = model.generate_content(summary_prompt, safety_settings=safety_settings)
        
        summary_match = re.search(r'\{.*\}', final_report_response.text, re.DOTALL)
        if summary_match: summary_data = json.loads(summary_match.group(0))
        else: summary_data = {"overall_verdict": "Error", "overall_assessment": "Failed to parse summary from AI.", "key_points": []}

        data_to_save_in_db = {
            "summary_data": summary_data, "verdict_counts": verdict_counts,
            "average_confidence": average_confidence, "detailed_results": all_results,
            "created_at": firestore.SERVER_TIMESTAMP 
        }
        
        doc_ref.set(data_to_save_in_db)
        data_to_return = data_to_save_in_db.copy()
        data_to_return["created_at"] = datetime.datetime.now().isoformat()
        
        return data_to_return

    except Exception as e:
        print(f"!!! [LOG-CRITICAL] Произошла критическая ошибка в задаче: {e}")
        raise e