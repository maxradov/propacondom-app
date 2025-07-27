# constants.py
# Максимальное число claims, которые можно извлечь из одного источника
MAX_CLAIMS_EXTRACTED = 10

# Максимум claims для проверки за раз (будет зависеть от подписки)
MAX_CLAIMS_TO_CHECK = 5

# Срок устаревания клейма для recheck (например, 30 дней)
CACHE_EXPIRATION_DAYS = 30

# === Blog Generation Settings ===
BLOG_POSTING_INTERVAL_MINUTES = 2880  # 1 раз в сутки. Для отладки можно поставить 10
WORDS_PER_SECTION = 150
SUMMARY_WORD_COUNT = 30
BLOG_SECTIONS_PER_ARTICLE = 4
PROMO_LINKS = [
    "https://factchecking.pro",
    "https://factchecking.pro/report/some-report-id", # Пример ссылки
    "https://factchecking.pro/about"
]