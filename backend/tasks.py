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

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
SEARCHAPI_KEY = os.environ.get('SEARCHAPI_KEY')

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

db = firestore.Client()

def get_video_id(url):
    """Reliably extracts the video ID from any YouTube URL variant."""
    if not url: return None
    regex = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None

@celery.task(bind=True, name='tasks.fact_check_video', time_limit=600)
def fact_check_video(self, video_url, target_lang='en'):
    try:
        video_id = get_video_id(video_url)
        if not video_id: raise ValueError(f"Could not extract video ID from URL: {video_url}")

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict()

        # --- Получение деталей видео (без изменений) ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Fetching video details...'})
        params_details = {
            'engine': 'youtube_video',
            'video_id': video_id,
            'api_key': SEARCHAPI_KEY
        }
        details_response = requests.get('https://www.searchapi.io/api/v1/search', params=params_details)
        details_response.raise_for_status()
        details_data = details_response.json()

        if 'error' in details_data:
            raise Exception(f"SearchApi.io (video details) error: {details_data['error']}")
        
        video_details = details_data.get('video_details', {})
        video_title = video_details.get('title', 'Title Not Found')
        thumbnails = video_details.get('thumbnails', [])
        thumbnail_url = ''
        if thumbnails:
            thumb_map = {thumb['quality']: thumb['url'] for thumb in thumbnails}
            thumbnail_url = thumb_map.get('high', thumb_map.get('medium', thumbnails[0].get('url', '')))

        # --- Получение транскрипции (без изменений) ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Checking available languages...'})
        params_list_langs = {'engine': 'youtube_transcripts', 'video_id': video_id, 'api_key': SEARCHAPI_KEY}
        response_list_langs = requests.get('https://www.searchapi.io/api/v1/search', params=params_list_langs)
        metadata = response_list_langs.json()
        if 'error' in metadata and 'available_languages' not in metadata:
             raise Exception(f"SearchApi.io returned an error: {metadata['error']}")
        available_langs = [lang.get('lang') for lang in metadata.get('available_languages', []) if lang.get('lang')]
        if not available_langs: raise ValueError("API did not find available subtitles in any language.")
        priority_langs = [target_lang, 'en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_langs), available_langs[0])
        self.update_state(state='PROGRESS', meta={'status_message': f'Extracting subtitles ({detected_lang})...'})
        params_get_transcript = {'engine': 'youtube_transcripts', 'video_id': video_id, 'lang': detected_lang, 'api_key': SEARCHAPI_KEY}
        response_transcript = requests.get('https://www.searchapi.io/api/v1/search', params=params_get_transcript)
        transcript_data = response_transcript.json()
        if 'error' in transcript_data or not transcript_data.get('transcripts'): raise ValueError(f"API did not return subtitles for '{detected_lang}'.")
        clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])

        # --- AI АНАЛИЗ С НОВЫМИ ПРОМПТАМИ ---
        self.update_state(state='PROGRESS', meta={'status_message': 'Analyzing text...'})
        model = genai.GenerativeModel('gemini-1.5-pro')
        safety_settings = {'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE', 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE'}

        # --- ИЗМЕНЕНИЕ 1: Улучшенный промпт для извлечения утверждений ---
        prompt_claims = f"Analyze the following transcript. Your task is to extract the 5 main factual claims that can be verified against public information. Focus on specific, checkable statements (e.g., statistics, events, concrete statements) and ignore subjective opinions or personal feelings. Present them as a numbered list. Transcript: --- {clean_text} ---"
        response_claims = model.generate_content(prompt_claims, safety_settings=safety_settings)
        claims_list = [re.sub(r'^\d+\.\s*', '', line).strip() for line in response_claims.text.strip().split('\n') if line.strip() and re.match(r'^\d+\.', line)]
        
        if not claims_list: raise ValueError("AI did not return claims in the expected format.")
        self.update_state(state='PROGRESS', meta={'status_message': f'Extracted {len(claims_list)} statements. Fact-checking...'})
        
        # --- ИЗМЕНЕНИЕ 2: Кардинально переработанный промпт для факт-чекинга ---
        prompt_fc = f"""You are a meticulous fact-checker. Your task is to verify each claim from the provided list using publicly available information on the internet. For each claim, you must return a single JSON object on a new line.

        Each JSON object must have these keys:
        - "claim": The original claim text.
        - "verdict": Your verdict. Must be one of these exact strings: "True", "False", "Misleading", "Partly True", "Unverifiable".
        - "confidence_percentage": An integer from 0 to 100 indicating your confidence in the verdict.
        - "explanation": A concise, neutral explanation of your findings and the reasoning for your verdict. Summarize the evidence you found.
        - "sources": A JSON list of URL strings for the high-authority sources you used. Provide at least one source if the claim is not "Unverifiable".

        **CRITICAL INSTRUCTIONS:**
        1.  Base your analysis ONLY on publicly verifiable information.
        2.  DO NOT state that you need "access to the speaker's statements" or any other private information. Your job is to check against public data.
        3.  If a claim cannot be verified using public search, the verdict must be "Unverifiable" and the explanation should state why (e.g., "No reliable public sources were found to confirm or deny this specific claim.").

        List of claims to check:
        {json.dumps(claims_list)}

        Respond in {target_lang}.
        """
        response_fc = model.generate_content(prompt_fc, safety_settings=safety_settings)
        
        all_results = []
        for line in response_fc.text.strip().split('\n'):
            try:
                all_results.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                print(f"Skipping malformed JSON line: {line}")
                continue
        
        if not all_results: raise ValueError("AI did not return any valid JSON objects for the fact-check.")
        self.update_state(state='PROGRESS', meta={'status_message': 'Generating final report...'})

        # --- Подсчет статистики (без изменений) ---
        verdict_counts = {"True": 0, "False": 0, "Misleading": 0, "Partly True": 0, "Unverifiable": 0}
        confidence_sum = 0
        claims_with_confidence = 0
        for res in all_results:
            verdict = res.get("verdict", "Unverifiable")
            if verdict in verdict_counts:
                verdict_counts[verdict] += 1
            else: # На случай, если ИИ вернет вердикт не из списка
                verdict_counts["Unverifiable"] += 1

            if "confidence_percentage" in res and isinstance(res["confidence_percentage"], int):
                confidence_sum += res["confidence_percentage"]
                claims_with_confidence += 1
        average_confidence = round(confidence_sum / claims_with_confidence) if claims_with_confidence > 0 else 0
        
        # --- ИЗМЕНЕНИЕ 3: Улучшенный промпт для итогового отчета ---
        summary_prompt = f"""Analyze the provided fact-checking results. Your task is to synthesize this information into a high-level summary. Return a single, clean JSON object with the following keys:
        - "overall_verdict": A brief, overall conclusion based on the verdicts. Choose one: "Mostly True", "Mostly False", "Mixed Veracity", "Largely Unverifiable".
        - "overall_assessment": A neutral, one-paragraph summary of the main findings. Explain the general nature of the claims (e.g., are they related to politics, science, etc.?) and the overall picture of their accuracy.
        - "key_points": A JSON list of 3-5 bullet points summarizing the most important or impactful findings from the fact-check.

        Fact-checking data:
        {json.dumps(all_results)}

        Respond in {target_lang}.
        """
        final_report_response = model.generate_content(summary_prompt, safety_settings=safety_settings)
        
        summary_match = re.search(r'\{.*\}', final_report_response.text, re.DOTALL)
        summary_data = json.loads(summary_match.group(0)) if summary_match else {}

        # --- Сохранение в БД (без изменений) ---
        data_to_save_in_db = {
            "video_url": video_url, "video_title": video_title, "thumbnail_url": thumbnail_url,
            "summary_data": summary_data, "verdict_counts": verdict_counts,
            "average_confidence": average_confidence, "detailed_results": all_results,
            "created_at": firestore.SERVER_TIMESTAMP 
        }
        
        doc_ref.set(data_to_save_in_db)
        return doc_ref.get().to_dict()

    except Exception as e:
        print(f"A critical error occurred in the task: {e}")
        raise e