# backend/celery_init.py
from celery import Celery

# Создаем "пустой" экземпляр Celery. Вся конфигурация будет в app.py
celery = Celery(__name__)