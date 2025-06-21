import os
import re
import json
import datetime
import requests
import google.generativeai as genai
from celery import Celery
from google.cloud import firestore
from urllib.parse import urlparse, parse_qs

# --- Settings ---
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
celery = Celery(__name__, broker=REDIS_URL, backend=REDIS_URL)

# Ключи все еще можно определить здесь для общей доступности, но в задаче мы их перечитаем
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
        # --- ИЗМЕНЕНИЕ 1: Добавляем логирование и надежное получение ключа API ---
        print(f"[TASK_START] Received URL: {video_url}")
        
        # Надежно получаем ключ API внутри задачи, как в старой рабочей версии
        search_api_key = os.environ.get('SEARCHAPI_KEY')
        if not search_api_key:
            raise ValueError("SEARCHAPI_KEY environment variable not found or empty inside the task.")
        
        print(f"[TASK_INFO] Using Search API Key ending with: ...{search_api_key[-4:]}")

        video_id = get_video_id(video_url)
        if not video_id: raise ValueError(f"Could not extract video ID from URL: {video_url}")
        
        print(f"[TASK_INFO] Extracted Video ID: {video_id}")

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        doc = doc_ref.get()
        if doc.exists:
            print(f"[TASK_SUCCESS] Cache hit for video_id {video_id}. Returning from Firestore.")
            return doc.to_dict()

        self.update_state(state='PROGRESS', meta={'status_message': 'Fetching video details...'})
        
        # --- ИЗМЕНЕНИЕ 2: Используем локальную переменную `search_api_key` во всех запросах ---
        params_details = {'engine': 'youtube_video','video_id': video_id,'api_key': search_api_key}
        details_response = requests.get('https://www.searchapi.io/api/v1/search', params=params_details)
        details_response.raise_for_status() # Убедимся, что запрос успешен
        details_data = details_response.json()
        
        if 'error' in details_data: raise Exception(f"SearchApi.io (video details) error: {details_data['error']}")
        
        video_details = details_data.get('video_details', {})
        video_title = video_details.get('title', 'Title Not Found')
        thumbnails = video_details.get('thumbnails', [])
        thumbnail_url = ''
        if thumbnails:
            thumb_map = {thumb['quality']: thumb['url'] for thumb in thumbnails}
            thumbnail_url = thumb_map.get('high', thumb_map.get('medium', thumbnails[0].get('url', '')))
        
        print(f"[TASK_INFO] Fetched Title: {' '.join(video_title.split()[:5])}...")

        # ... остальная часть кода также должна использовать `search_api_key` ...
        self.update_state(state='PROGRESS', meta={'status_message': 'Checking available languages...'})
        params_list_langs = {'engine': 'youtube_transcripts', 'video_id': video_id, 'api_key': search_api_key}
        # ... и так далее для всех остальных вызовов requests.get

        # (Весь остальной код анализа и сохранения остается таким же, как в последней версии,
        # просто убедитесь, что все вызовы к searchapi.io используют `search_api_key`)
        response_list_langs = requests.get('https://www.searchapi.io/api/v1/search', params=params_list_langs)
        metadata = response_list_langs.json()
        if 'error' in metadata and 'available_languages' not in metadata: raise Exception(f"SearchApi.io returned an error: {metadata['error']}")
        available_langs = [lang.get('lang') for lang in metadata.get('available_languages', []) if lang.get('lang')]
        if not available_langs: raise ValueError("API did not find available subtitles in any language.")
        priority_langs = [target_lang, 'en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_langs), available_langs[0])
        self.update_state(state='PROGRESS', meta={'status_message': f'Extracting subtitles ({detected_lang})...'})
        params_get_transcript = {'engine': 'youtube_transcripts', 'video_id': video_id, 'lang': detected_lang, 'api_key': search_api_key}
        response_transcript = requests.get('https://www.searchapi.io/api/v1/search', params=params_get_transcript)
        transcript_data = response_transcript.json()
        if 'error' in transcript_data or not transcript_data.get('transcripts'): raise ValueError(f"API did not return subtitles for '{detected_lang}'.")
        clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])

        # ... (Код AI-анализа, подсчета статистики и сохранения в БД остается без изменений) ...
        self.update_state(state='PROGRESS', meta={'status_message': 'Analyzing text...'})
        model = genai.GenerativeModel('gemini-1.5-pro')
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}

        prompt_claims = f"Analyze the following transcript. Your task is to extract the 5 main factual claims that can be verified against public information. Focus on specific, checkable statements (e.g., statistics, events, concrete statements) and ignore subjective opinions or personal feelings. Present them as a numbered list. Transcript: --- {clean_text} ---"
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip() and re.match(r'^\d+\.', line)]
        
        if not claims_list: raise ValueError("AI did not return claims in the expected format.")
        self.update_state(state='PROGRESS', meta={'status_message': f'Extracted {len(claims_list)} statements. Fact-checking...'})
        
        prompt_fc = f"""You are a meticulous fact-checker. Your task is to verify each claim from the provided list using publicly available information on the internet. For each claim, you must return a single JSON object on a new line. Each JSON object must have these keys: "claim", "verdict", "confidence_percentage", "explanation", "sources". The "verdict" must be one of these exact strings: "True", "False", "Misleading", "Partly True", "Unverifiable". "explanation" should be a concise, neutral summary of your findings. "sources" should be a JSON list of URL strings. **CRITICAL INSTRUCTIONS:** 1. Base your analysis ONLY on publicly verifiable information. 2. DO NOT state that you need "access to the speaker's statements". Your job is to check against public data. 3. If a claim cannot be verified, the verdict must be "Unverifiable". Respond in {target_lang}."""
        response_fc = model.generate_content(prompt_fc, safety_settings=safety_settings)
        
        all_results = []
        for line in response_fc.text.strip().split('\n'):
            try: all_results.append(json.loads(line.strip()))
            except json.JSONDecodeError: print(f"Skipping malformed JSON line: {line}"); continue
        
        if not all_results: raise ValueError("AI did not return any valid JSON objects for the fact-check.")
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
        
        summary_prompt = f"""Analyze the provided fact-checking results. Your task is to synthesize this information into a high-level summary. Return a single, clean JSON object with the following keys: "overall_verdict", "overall_assessment", "key_points". "overall_verdict" must be one of: "Mostly True", "Mostly False", "Mixed Veracity", "Largely Unverifiable". Respond in {target_lang}. Data: {json.dumps(all_results)}"""
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
        print(f"[TASK_SUCCESS] Task for video_id {video_id} completed and saved to Firestore.")
        return doc_ref.get().to_dict()

    except Exception as e:
        print(f"!!! [TASK_CRITICAL] A critical error occurred in the task: {e}")
        raise e