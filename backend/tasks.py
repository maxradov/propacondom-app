import os
import re
import json
import datetime
import requests # <-- Using the new library
import google.generativeai as genai
from celery import Celery
from google.cloud import firestore
from urllib.parse import urlparse, parse_qs

# --- Settings ---
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
celery = Celery(__name__, broker=REDIS_URL, backend=REDIS_URL)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
SEARCHAPI_KEY = os.environ.get('SEARCHAPI_KEY') # <-- Getting the new key

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

db = firestore.Client()

def get_video_id(url):
    """Reliably extracts the video ID from any YouTube URL variant."""
    if not url: return None
    regex = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None

@celery.task(bind=True, time_limit=600)
def fact_check_video(self, video_url, target_lang='en'):
    try:
        video_id = get_video_id(video_url)
        if not video_id: raise ValueError(f"Could not extract video ID from URL: {video_url}")

        doc_ref = db.collection('analyses').document(f"{video_id}_{target_lang}")
        doc = doc_ref.get()
        if doc.exists:
            print(f"[LOG-SUCCESS] Found and returned cache from Firestore.")
            cached_data = doc.to_dict()
            if 'created_at' in cached_data and hasattr(cached_data['created_at'], 'isoformat'):
                 cached_data['created_at'] = cached_data['created_at'].isoformat()
            return cached_data

        # --- START OF CHANGES IN THIS BLOCK ---
        print(f"[LOG-STEP 2] Requesting metadata and transcripts via SearchApi.io...")
        self.update_state(state='PROGRESS', meta={'status_message': 'Checking available languages...'})

        api_key = os.environ.get('SEARCHAPI_KEY')
        # Requesting transcript and metadata at the same time
        params = {
            'engine': 'youtube_transcripts', 'video_id': video_id, 'api_key': api_key
        }
        response = requests.get('https://www.searchapi.io/api/v1/search', params=params)
        response.raise_for_status()
        metadata = response.json()

        if 'error' in metadata and 'available_languages' not in metadata:
             raise Exception(f"SearchApi.io returned an error: {metadata['error']}")

        # Extracting video title and thumbnail from metadata
        video_title = metadata.get('video_details', {}).get('title', 'Video Title Not Found')
        thumbnail_url = metadata.get('video_details', {}).get('thumbnail', '')
        
        available_langs = [lang.get('lang') for lang in metadata.get('available_languages', []) if lang.get('lang')]
        if not available_langs: raise ValueError("API did not find available subtitles in any language.")
        
        priority_langs = [target_lang, 'en', 'ru', 'uk']
        detected_lang = next((lang for lang in priority_langs if lang in available_langs), available_langs[0])
        
        self.update_state(state='PROGRESS', meta={'status_message': f'Extracting subtitles ({detected_lang})...'})
        
        # If the transcript for the desired language didn't arrive with the first request, make a second one
        if not metadata.get('transcripts'):
            params['lang'] = detected_lang
            response_transcript = requests.get('https://www.searchapi.io/api/v1/search', params=params)
            response_transcript.raise_for_status()
            transcript_data = response_transcript.json()
        else:
            transcript_data = metadata

        if 'error' in transcript_data: raise Exception(f"SearchApi.io returned an error: {transcript_data['error']}")
        if not transcript_data.get('transcripts'): raise ValueError(f"API did not return subtitles for language '{detected_lang}'.")

        clean_text = " ".join([item['text'] for item in transcript_data['transcripts']])
        # --- END OF CHANGES IN THIS BLOCK ---

        # ... (The rest of the code with Gemini calls and stats calculation remains unchanged) ...
        # ... ... ...

        data_to_save_in_db = {
            "video_url": video_url, 
            "video_title": video_title,
            "thumbnail_url": thumbnail_url,
            "summary_data": summary_data,
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
        print(f"!!! [LOG-CRITICAL] A critical error occurred in the task: {e}")
        raise e