"""Настройки поиска hh.ru — меняй под себя."""

# Поисковые запросы — в заголовке вакансии (name)
SEARCH_QUERIES = [
    "product manager",
    "продакт-менеджер",
    "product owner",
    "менеджер продукта",
]

# Коды регионов: 1=Москва, 2=СПб, 160=Алматы, 40=Вся Россия
AREAS = ["1", "2", "160"]

PER_PAGE = 20
PAGES = 2  # 2 страницы × 20 = до 40 вакансий на запрос

# Опыт: noExperience, between1And3, between3And6, moreThan6
EXPERIENCE = "between1And3"

# Искать ТОЛЬКО в названии вакансии (не в описании)
SEARCH_FIELD = "name"
