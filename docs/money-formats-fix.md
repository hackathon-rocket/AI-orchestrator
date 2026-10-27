# Task 3 — денежные форматы

## Изменения

- `app/validators/financial.py`, `parse_money()`: если присутствуют точка и запятая, последний разделитель является десятичным только при полном соответствии остальных групп тройкам цифр. Проверка выполняется до удаления разделителей. Число преобразуется в Decimal; прежние ограничения finite, максимум 10^12 по модулю и максимум два десятичных знака сохранены.
- `app/validators/evidence.py`, только денежная ветка `literal_support()`: берётся максимальная последовательность цифр/разделителей, затем целиком проверяется через существующий `equivalent()` и `parse_money()`. Нет отката к корректному фрагменту неправильного числа. Добавлены узкие неразрывные пробелы и положительный знак. Ветка quantity сохранена отдельно без изменения её шаблона.
- `tests/unit/test_money_formats.py`: 91 новый случай.
- `tests/unit/test_validators.py`: `1,234.56` перенесён из отвергаемых форматов в поддерживаемые с точным ожидаемым результатом.

`validation.py`, equivalent(), Excel-геометрия, OCR-контекст сторон, классификация, mapping/config, providers и API не изменены. AST-сравнение подтвердило изменение только parse_money и literal_support в производственном коде.

## Причина и результат

Старый парсер разрешал группировку только пробелами, а шаблон evidence допускал только один пунктуационный разделитель. Поэтому корректная сумма не нормализовалась или её полный токен не распознавался.

| Формат | До | После |
|---|---|---|
| 1.234,50 EUR | Отклонён | Decimal("1234.50") |
| 1,234.50 EUR | Отклонён | Decimal("1234.50") |
| 1.234.567,89 EUR | Отклонён | Decimal("1234567.89") |

Все десять обязательных форматов проверены. Сохраняются знаки, EUR/€/CZK/USD/GBP/CHF/PLN/HUF, NBSP, narrow NBSP и Decimal. Значения 1.234, 1,234, неправильные группы, NaN, Infinity, дроби длиннее двух знаков и превышение лимита отвергаются.

## BBSK

Тесты выполняют реальную цепочку ExtractionData → validate_extraction → MappingEngine.map с `bbsk_11824` / `bbsk_11824_default` для обоих форматов 1.234,50 EUR и 1,234.50 EUR:

- document_type=invoice; classification.status=verified;
- total_gross.value=Decimal("1234.50"), status=verified, _safe_to_suggest=True;
- таблица rozpocet.zoznamuctovnychdokladov: typ=FA, suma_doklad=1234.5 (числовое поле существующего UI-контракта).

Неверное значение, неправильная роль, неоднозначный формат, конкурирующий итог и несогласованные net/VAT/gross не дают доверенного suma_doklad. Совпадающие суммы в разных форматах не создают ложного конфликта. Нормализация и сравнения выполняются в Decimal; преобразование в UI-число остаётся в неизменённом mapper.

## Выполненные проверки

Использовался `uv run --no-sync` с существующим work/.venv, UV_PROJECT_ENVIRONMENT и UV_CACHE_DIR; pytest cache_dir направлен в ../../work/money-cache, временные каталоги — в work.

- pytest tests/unit/test_money_formats.py tests/unit/test_validators.py -q: 122 passed.
- pytest tests/unit/test_validators.py tests/unit/test_validation_regressions.py tests/unit/test_mapping.py -q: 62 passed.
- pytest tests/unit/: 335 passed, 2.76 s.
- pytest: 432 passed, 13.00 s; включает Task 1, Task 2 и классификацию invoice.
- ruff check .: успешно.
- ruff format --check .: успешно, 74 файла.
- python -m compileall -q app: успешно.
- HTTP smoke в рабочей копии с актуальными validators: authenticated POST 200, unauthenticated POST 401, health/ready/docs/openapi 200, Excel endpoints 200, provider=mock.

Недоступных проверок нет. Реальные LLM API не вызывались.

## Ограничения

Поддерживается ограниченный набор однозначных форматов. Пунктуационная группировка без дробной части, смешанная группировка пробелами и точками/запятыми, экспоненциальная запись и дополнительные десятичные знаки не нормализуются догадками. Tokenizer консервативен к соседним числам без текстового разделителя. Валюта не конвертируется; источник и смысловая роль по-прежнему обязательны, пользователь подтверждает результат.

**FIXED AND VERIFIED**.
