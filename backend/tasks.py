import os
import re
import json
import datetime
import requests
import google.generativeai as genai
from celery import Celery
from google.cloud import firestore

# --- Settings ---
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
celery = Celery('tasks', broker=REDIS_URL, backend=REDIS_URL)

# --- Ключи API ---
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
# Старый ключ больше не нужен для факт-чекинга, только для получения субтитров
SEARCHAPI_KEY = os.environ.get('SEARCHAPI_KEY') 
# --- НОВЫЕ КЛЮЧИ ДЛЯ GOOGLE SEARCH ---
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
SEARCH_ENGINE_ID = os.environ.get('SEARCH_ENGINE_ID')

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

db = firestore.Client()

def get_video_id(url):
    if not url: return None
    regex = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None

@celery.task(bind=True, name='tasks.fact_check_video', time_limit=900) # Увеличим лимит времени
def fact_check_video(self, video_url, target_lang='en'):
    try:
        if not SEARCHAPI_KEY: raise ValueError("SEARCHAPI_KEY for subtitles not found.")
        if not GOOGLE_API_KEY: raise ValueError("GOOGLE_API_KEY for search not found.")
        if not SEARCH_ENGINE_ID: raise ValueError("SEARCH_ENGINE_ID for search not found.")

        video_id = get_video_id(video_url)
        if not video_id: raise ValueError(f"Could not extract video ID from URL: {video_url}")

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        doc = doc_ref.get()
        if doc.exists:
            cached_data = doc.to_dict()
            if 'created_at' in cached_data and hasattr(cached_data['created_at'], 'isoformat'):
                 cached_data['created_at'] = cached_data['created_at'].isoformat()
            return cached_data

        # --- Этап 1: Получение деталей видео и субтитров (без изменений) ---
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

        # --- Этап 2: Извлечение утверждений из текста (без изменений) ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Analyzing text...'})
        model = genai.GenerativeModel('gemini-1.5-pro-latest') # Используем последнюю модель
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}
        prompt_claims = f"Analyze the transcript. Extract 5 main factual claims that can be verified. Focus on specific, checkable statements. Present them as a numbered list. Transcript: --- {clean_text} ---"
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip() and re.match(r'^\d+\.', line)]
        if not claims_list: raise ValueError("AI did not return claims in the expected format.")
        
        self.update_state(state='PROGRESS', meta={'status_message': f'Extracted {len(claims_list)} statements. Fact-checking...'})

        # --- ЭТАП 3: НОВАЯ ЛОГИКА - ПРОВЕРКА КАЖДОГО УТВЕРЖДЕНИЯ ЧЕРЕЗ GOOGLE SEARCH API ---
        all_results = []
        for claim in claims_list:
            print(f"--- Checking claim: {claim} ---")
            
            # 1. Делаем поиск в Google
            search_params = {'q': claim, 'key': GOOGLE_API_KEY, 'cx': SEARCH_ENGINE_ID, 'num': 4}
            search_response = requests.get("https://www.googleapis.com/customsearch/v1", params=search_params)
            search_results = search_response.json().get('items', [])
            
            # 2. Формируем контекст для AI
            search_context = "No relevant search results found."
            if search_results:
                search_context = ""
                for i, result in enumerate(search_results):
                    search_context += f"Source {i+1}: URL: {result.get('link')}, Snippet: {result.get('snippet')}\n"
            
            print(f"Search context for AI: {search_context}")

            # 3. Отправляем утверждение и результаты поиска в AI для вынесения вердикта
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
            - The "explanation" MUST be a concise, neutral summary of the evidence from the search results, written in {target_lang}.
            - The "sources" MUST be a list of the URLs from the search results that you used for your conclusion.
            - Your entire response must be ONLY a single JSON object.
            """
            fc_response = model.generate_content(prompt_fc, safety_settings=safety_settings)
            
            # Парсинг ответа
            match = re.search(r'\{.*\}', fc_response.text, re.DOTALL)
            if not match:
                # Если AI не вернул JSON, создаем запись об ошибке
                result_item = {"claim": claim, "verdict": "Unverifiable", "confidence_percentage": 0, "explanation": "AI failed to provide a valid analysis.", "sources": []}
            else:
                result_item = json.loads(match.group(0))
            
            all_results.append(result_item)

        # --- ЭТАП 4: ФОРМИРОВАНИЕ ИТОГОВОГО ОТЧЕТА ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Generating final report...'})

        # --- Блок подсчета Статистики ---
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
        
        # --- УЛУЧШЕНИЕ: Блок summary_prompt ---
        # Передаем только вердикты и утверждения, а не все данные, для эффективности
        summary_context = [{"claim": res.get("claim"), "verdict": res.get("verdict")} for res in all_results]
        
        summary_prompt = f"""Analyze the fact-checking results. Return a single, clean JSON object.
        It must have these keys: "overall_verdict", "overall_assessment", and "key_points".
        - "overall_verdict" must be one of: "Mostly True", "Mostly False", "Mixed Veracity", "Largely Unverifiable".
        - "overall_assessment" must be a neutral, one-paragraph summary in {target_lang}.
        - "key_points" must be a JSON array of simple STRINGS (in {target_lang}). Each string is a key takeaway.
        Data: {json.dumps(summary_context)}
        """
        final_report_response = model.generate_content(summary_prompt, safety_settings=safety_settings)
        
        summary_match = re.search(r'\{.*\}', final_report_response.text, re.DOTALL)
        if summary_match:
            summary_data = json.loads(summary_match.group(0))
        else:
            summary_data = {}

        # --- Код сохранения в БД ---
        data_to_save_in_db = {
            "video_url": video_url, "video_title": video_title, "thumbnail_url": thumbnail_url,
            "summary_data": summary_data, "verdict_counts": verdict_counts,
            "average_confidence": average_confidence,
            "confirmed_credibility": confirmed_credibility, 
            "detailed_results": all_results,
            "created_at": firestore.SERVER_TIMESTAMP 
        }
        
        doc_ref.set(data_to_save_in_db)

        # --- ИСПРАВЛЕНИЕ: ВОССТАНОВЛЕНА недостающая строка return ---
        data_to_return = data_to_save_in_db.copy()
        data_to_return["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return data_to_return

    except Exception as e:
        # --- Код обработки исключений ---
        print(f"!!! [TASK_CRITICAL] A critical error occurred in the task: {e}")
        self.update_state(
            state='FAILURE',
            meta={
                'exc_type': type(e).__name__,
                'exc_message': str(e)
            }
        )
        raise e