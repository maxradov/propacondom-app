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
celery = Celery(__name__, broker=REDIS_URL, backend=REDIS_URL)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

db = firestore.Client()

def get_video_id(url):
    if not url: return None
    regex = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None

@celery.task(bind=True, name='tasks.fact_check_video', time_limit=600)
def fact_check_video(self, video_url, target_lang='en'):
    try:
        search_api_key = os.environ.get('SEARCHAPI_KEY')
        if not search_api_key:
            raise ValueError("SEARCHAPI_KEY environment variable not found.")

        # --- ИСПРАВЛЕНИЕ: ЭТА СТРОКА ПЕРЕМЕЩЕНА ВВЕРХ ---
        video_id = get_video_id(video_url)
        if not video_id:
            raise ValueError(f"Could not extract video ID from URL: {video_url}")

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        if doc_ref.get().exists:
            return doc_ref.get().to_dict()
    
    try:
         # --- 2. ПОЛУЧЕНИЕ ДЕТАЛЕЙ ВИДЕО (ИСПРАВЛЕННАЯ ЛОГИКА) ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Fetching video details...'})
        params_details = {'engine': 'youtube_video', 'video_id': video_id, 'api_key': search_api_key}
        details_response = requests.get('https://www.searchapi.io/api/v1/search', params=params_details)
        details_response.raise_for_status()
        details_data = details_response.json()

        # --- ИЗМЕНЕНИЕ ЗДЕСЬ: Ищем "context", а не "video_details" ---
        context_data = details_data.get('context')

        if not context_data:
            print("!!! [API_DEBUG] 'context' key not found in SearchApi.io response.")
            print(f"!!! [API_DEBUG] Full response for details: {json.dumps(details_data)}")
            video_title = 'Title Not Found'
            thumbnail_url = ''
        else:
            # Получаем данные из 'context', как в вашем примере
            video_title = context_data.get('title', 'Title Not Found')
            thumbnail_url = context_data.get('thumbnail', '')
       

        # --- 3. ПОЛУЧЕНИЕ СУБТИТРОВ (ВОССТАНОВЛЕННАЯ РАБОЧАЯ ЛОГИКА) ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Checking available languages...'})
        params_list_langs = {'engine': 'youtube_transcripts', 'video_id': video_id, 'api_key': search_api_key}
        response_list_langs = requests.get('https://www.searchapi.io/api/v1/search', params=params_list_langs)
        response_list_langs.raise_for_status()
        metadata = response_list_langs.json()

        if 'error' in metadata and 'available_languages' not in metadata:
             raise Exception(f"SearchApi.io returned an error: {metadata['error']}")

        available_langs = [lang.get('lang') for lang in metadata.get('available_languages', []) if lang.get('lang')]
        if not available_langs:
            raise ValueError("The API did not find any available subtitles in any language.")
        
        priority_langs = [target_lang, 'en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_langs), available_langs[0])
        
        self.update_state(state='PROGRESS', meta={'status_message': f'Extracting subtitles ({detected_lang})...'})
        
        params_get_transcript = {'engine': 'youtube_transcripts', 'video_id': video_id, 'lang': detected_lang, 'api_key': search_api_key}
        response_transcript = requests.get('https://www.searchapi.io/api/v1/search', params=params_get_transcript)
        response_transcript.raise_for_status()
        transcript_data = response_transcript.json()
        
        if 'error' in transcript_data:
            raise Exception(f"SearchApi.io returned an error: {transcript_data['error']}")
        if not transcript_data.get('transcripts'):
            raise ValueError(f"API did not return subtitles for language '{detected_lang}'.")

        clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])

        # --- 4. AI-АНАЛИЗ И СОХРАНЕНИЕ (используем новые улучшенные промпты) ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Analyzing text...'})
        model = genai.GenerativeModel('gemini-1.5-pro')
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}

        prompt_claims = f"Analyze the following transcript. Your task is to extract the 5 main factual claims that can be verified against public information. Focus on specific, checkable statements. Present them as a numbered list. Transcript: --- {clean_text} ---"
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip() and re.match(r'^\d+\.', line)]
        
        if not claims_list: raise ValueError("AI did not return claims in the expected format.")
        
        self.update_state(state='PROGRESS', meta={'status_message': f'Extracted {len(claims_list)} statements. Fact-checking...'})
        
        prompt_fc = f"""You are a meticulous fact-checker. For the provided list of claims, return a single, valid JSON array of objects. Each object in the array must have these exact keys: "claim", "verdict", "confidence_percentage", "explanation", "sources". "verdict" must be one of: "True", "False", "Misleading", "Partly True", "Unverifiable". "explanation" must be a concise, neutral summary of your findings. "sources" must be a JSON list of URL strings. CRITICAL: Your entire response must be ONLY the JSON array, starting with `[` and ending with `]`. Do not add any other text or markdown. Claims to check: {json.dumps(claims_list)}. Respond in {target_lang}."""
        response_fc = model.generate_content(prompt_fc, safety_settings=safety_settings)
        
        match = re.search(r'\[\s*\{.*\}\s*\]', response_fc.text, re.DOTALL)
        if not match: raise ValueError("AI response does not contain a valid JSON array.")
        all_results = json.loads(match.group(0))

        if not all_results: raise ValueError("Fact-check parsing resulted in an empty list.")
            
        self.update_state(state='PROGRESS', meta={'status_message': 'Generating final report...'})

        verdict_counts = {"True": 0, "False": 0, "Misleading": 0, "Partly True": 0, "Unverifiable": 0}
        confidence_sum = 0
        claims_with_confidence = 0
        for res in all_results:
            verdict = res.get("verdict", "Unverifiable")
            if verdict in verdict_counts: verdict_counts[verdict] += 1
            else: verdict_counts["Unverifiable"] += 1
            if "confidence_percentage" in res and isinstance(res["confidence_percentage"], int):
                confidence_sum += res["confidence_percentage"]
                claims_with_confidence += 1
        average_confidence = round(confidence_sum / claims_with_confidence) if claims_with_confidence > 0 else 0
        true_verdicts = verdict_counts.get("True", 0)
        total_verdicts = len(all_results)
        confirmed_credibility = round((true_verdicts / total_verdicts) * 100) if total_verdicts > 0 else 0
        
        summary_prompt = f"""Analyze the fact-checking results. Return a single, clean JSON object. It must have these keys: "overall_verdict", "overall_assessment", and "key_points". "overall_verdict" must be one of: "Mostly True", "Mostly False", "Mixed Veracity", "Largely Unverifiable". "overall_assessment" must be a neutral, one-paragraph summary. "key_points" must be a JSON array of simple STRINGS. Each string is a key takeaway. Data: {json.dumps(all_results)}. Respond in {target_lang}."""
        final_report_response = model.generate_content(summary_prompt, safety_settings=safety_settings)
        
        summary_match = re.search(r'\{.*\}', final_report_response.text, re.DOTALL)
        summary_data = json.loads(summary_match.group(0)) if summary_match else {}

        data_to_save_in_db = {
            "video_url": video_url, "video_title": video_title, "thumbnail_url": thumbnail_url,
            "summary_data": summary_data, "verdict_counts": verdict_counts,
            "average_confidence": average_confidence,
            "confirmed_credibility": confirmed_credibility, 
            "detailed_results": all_results,
            "created_at": firestore.SERVER_TIMESTAMP 
        }
        
        doc_ref.set(data_to_save_in_db)
        return doc_ref.get().to_dict()

    except Exception as e:
        print(f"!!! [TASK_CRITICAL] A critical error occurred in the task: {e}")
        raise e