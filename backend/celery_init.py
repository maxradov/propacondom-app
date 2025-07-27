# celery_init.py
import os
from celery import Celery
from constants import BLOG_POSTING_INTERVAL_MINUTES

# Получаем REDIS_URL из переменных окружения
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Создаем экземпляр Celery
celery = Celery(__name__, broker=REDIS_URL, backend=REDIS_URL)

# --- НОВАЯ КОНФИГУРАЦИЯ ---
celery.conf.update(
    # Явно указываем Celery, где искать модули с задачами.
    # Он будет автоматически искать файл tasks.py.
    imports=('tasks',),

    # Устанавливаем часовой пояс для корректной работы расписания
    timezone='UTC',
    enable_utc=True,

    # Добавляем наше расписание сюда
    beat_schedule={
        'generate-periodic-blog-article': {
            'task': 'tasks.generate_and_publish_article',
            'schedule': 60.0 * BLOG_POSTING_INTERVAL_MINUTES,
        },
    }
)