# Исправление классификации invoice

## Изменения

- `app/validators/evidence.py`: добавлены `primary_heading_lines()` и `primary_heading_types()`; OCR-ветка `role_support()` проверяет пересечение каждой найденной цитаты со словами распознанного заголовка. Один номер документа не доказывает тип. Существующий `heading_types()` и Excel-ветка не изменены.
- `app/validators/validation.py`: удалена проверка, требовавшая совпадения цитаты с началом OCR-строки; её заменяет проверка исходного заголовка в `role_support()`. Конфликты OCR классификации используют первичные заголовки. Проверки Excel остались прежними.
- `tests/unit/test_invoice_classification.py`: 22 новых случая.

## Причины и результат

Частичная цитата `faktúra č. 2026/017` из `Daňový doklad - faktúra č. 2026/017` раньше блокировалась проверкой начала строки. Теперь она подтверждается исходным заголовком и точным совпадением на указанной странице.

`Zmluva č. Z-2026/9` после суммы invoice раньше считалась конкурирующим заголовком. Теперь узкое правило распознаёт номерную ссылку внутри непрерывного блока реквизитов invoice: на той же странице, после известного заголовка invoice и строки итога, без пустого разделителя и без последующего предмета/даты/суммы договора. Верхний регистр названия, отдельный блок/страница и содержательные реквизиты договора сохраняют конфликт. Саму вторичную ссылку нельзя использовать для подтверждения contract.

Это структурная эвристика, а не достоверное восстановление визуального макета. Необычные ссылки могут оставаться конфликтными; непрерывный блок с неполным договором, неотличимым от ссылки по OCR, остаётся ограничением. Требуется подтверждение человеком.

## BBSK

Реальные `bbsk_11824` / `bbsk_11824_default`, без изменения mapper:

| Сценарий | document_type | classification.status | Таблица | typ | suma_doklad |
|---|---|---|---|---|---|
| Полный заголовок | invoice | verified | rozpocet.zoznamuctovnychdokladov | FA | 123.0 |
| Частичная цитата | invoice | verified | rozpocet.zoznamuctovnychdokladov | FA | 123.0 |
| Многострочный заголовок | invoice | verified | rozpocet.zoznamuctovnychdokladov | FA | 123.0 |
| Invoice со ссылкой на договор | invoice | verified | rozpocet.zoznamuctovnychdokladov | FA | 123.0 |

Сфабрикованные цитаты, неверная страница, номер без названия, проза и настоящие конфликты дают unknown и пустой mapping. Результаты проверены assert-ами реального validate_extraction → MappingEngine.map.

## Проверки

До изменения: 3 новых теста падали, 19 проходили.

После изменения:

- `uv run --no-sync pytest tests/unit/test_invoice_classification.py tests/unit/test_validation_regressions.py tests/unit/test_semantic_adversarial.py tests/unit/test_mapping.py -q`: 62 passed.
- `uv run --no-sync pytest tests/unit/ -q`: 244 passed.
- `uv run --no-sync pytest`: 341 passed, 5.96 s; включает Task 1 и Task 2.
- `uv run --no-sync ruff check .`: успешно после удаления неиспользуемого импорта.
- `uv run --no-sync ruff format --check .`: 72 файла соответствуют форматированию.
- `uv run --no-sync python -m compileall -q app`: успешно.
- HTTP smoke в рабочей копии с обновлёнными validators: mock, authenticated POST 200, unauthenticated POST 401, health/ready/docs/openapi 200, XLSX 200.

Pytest запускался с cache_dir=../../work/classification-cache и отдельными basetemp внутри work. Использовалось существующее окружение work/.venv, UV_PROJECT_ENVIRONMENT указывал на него. Никаких платных LLM-вызовов. Недоступных проверок нет.

AST-проверка подтвердила, что observations, quote_support, heading_types и Excel-индексация не изменились. Финансовый парсинг, mapping, провайдеры и API-схемы не изменены.

**FIXED AND VERIFIED** для двух воспроизведённых дефектов и проверенных регрессий.
