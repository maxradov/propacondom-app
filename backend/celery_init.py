# celery_init.py
import os
from celery import Celery

# Получаем REDIS_URL из переменных окружения
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Создаем и экспортируем экземпляр Celery
celery = Celery(__name__, broker=REDIS_URL, backend=REDIS_URL)