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

        print(f"[LOG-START] Запущена задача для video_id: {video_id}, язык: {target_lang}")
        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        
        doc = doc_ref.get()
        if doc.exists:
            print(f"[LOG-SUCCESS] Найден и возвращен кэш из Firestore.")
            cached_data = doc.to_dict()
            if 'created_at' in cached_data and hasattr(cached_data['created_at'], 'isoformat'):
                 cached_data['created_at'] = cached_data['created_at'].isoformat()
            return cached_data

        # --------------------------------------------------------------------
        # --- НОВЫЙ БЛОК: Запрос субтитров через SearchApi.io ---
        # --------------------------------------------------------------------
        print(f"[LOG-STEP 2] Запрос субтитров через SearchApi.io...")
        self.update_state(state='PROGRESS', meta={'status_message': 'Извлечение субтитров через API...'})
        
        params = {
            'engine': 'youtube_transcripts',
            'video_id': video_id,
            'lang': target_lang,
            'api_key': SEARCHAPI_KEY
        }
        
        response = requests.get('https://www.searchapi.io/api/v1/search', params=params)
        response.raise_for_status() # Вызовет ошибку, если HTTP-статус не 2xx

        data = response.json()
        if 'error' in data:
            raise Exception(f"SearchApi.io вернул ошибку: {data['error']}")
        
        if not data.get('transcripts'):
            raise ValueError("API не вернул субтитры для этого видео.")
            
        # Собираем все кусочки текста в один большой блок
        clean_text = " ".join([item['text'] for item in data['transcripts']])
        print(f"[LOG-SUCCESS] Субтитры успешно получены через API.")
        # --------------------------------------------------------------------
        # --- КОНЕЦ НОВОГО БЛОКА ---
        # --------------------------------------------------------------------
        
        self.update_state(state='PROGRESS', meta={'status_message': 'Анализ текста...'})
        model = genai.GenerativeModel('gemini-1.5-flash')
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}

        print(f"[LOG-STEP 4] Вызов Gemini для извлечения утверждений...")
        prompt_claims = f"Analyze the following transcript. Extract up to 10 main factual claims as a numbered list. Transcript: --- {clean_text} ---"
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line) for line in response_claims.text.strip().split('\n')]
        claims_to_check = claims_list[:10]
        total_claims = len(claims_to_check)

        self.update_state(state='PROGRESS', meta={'status_message': f'Извлечено {total_claims} утверждений. Начинаю факт-чекинг...'})
        
        all_results = []
        print(f"[LOG-STEP 5] Старт цикла факт-чекинга...")
        for claim in claims_to_check:
            if not claim: continue
            prompt_fc = f'Fact-check this claim: "{claim}". Return ONLY a JSON object with keys: "claim", "verdict" (True, False, Partly True/Manipulation, No data), "confidence_percentage" (0-100), "explanation" (in {target_lang}), "sources" (array of URLs).'
            response_fc = model.generate_content(prompt_fc, safety_settings=safety_settings)
            json_match = re.search(r'\{.*\}', response_fc.text, re.DOTALL)
            if json_match:
                all_results.append(json.loads(json_match.group(0)))
            else:
                all_results.append({"claim": claim, "verdict": "Processing Error", "explanation": "AI did not return valid JSON."})
        
        self.update_state(state='PROGRESS', meta={'status_message': 'Формирование итогового отчета...'})

        # ... (остальная часть файла с подсчетом статистики и сохранением остается без изменений) ...

        verdict_counts = {"True": 0, "False": 0, "Unverifiable": 0}
        confidence_sum = 0
        claims_with_confidence = 0

        for res in all_results:
            verdict = res.get("verdict", "Unverifiable")
            if verdict == "True":
                verdict_counts["True"] += 1
            elif verdict == "False":
                verdict_counts["False"] += 1
            else: 
                verdict_counts["Unverifiable"] += 1
            
            if "confidence_percentage" in res:
                confidence_sum += int(res["confidence_percentage"])
                claims_with_confidence += 1
        
        average_confidence = round(confidence_sum / claims_with_confidence) if claims_with_confidence > 0 else 0
        
        # Найдите этот блок в tasks.py
print(f"[LOG-STEP 7] Вызов Gemini для генерации итогового отчета...")

        #  НОВЫЙ ПРОМПТ
        summary_prompt = f"""
        You are an editor-in-chief for a fact-checking agency. 
        Your task is to write a final summary report in plain text based on the provided JSON data.

        **IMPORTANT INSTRUCTIONS:**
        - Your entire response MUST be the prose report.
        - DO NOT use JSON or Markdown in your output.
        - Start your response directly with the "Overall Verdict".
        - The report MUST be in the language with code: '{target_lang}'.

        Report structure: 
        1. Overall Verdict. 
        2. Overall Assessment (2-3 sentences). 
        3. Key Points. 

        Data: 
        {json.dumps(all_results, ensure_ascii=False)}
        """

        final_report_response = model.generate_content(summary_prompt, safety_settings=safety_settings)
        
        data_to_save_in_db = {
            "summary_html": final_report_response.text,
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