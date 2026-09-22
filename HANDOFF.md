# Что сделано и что осталось — план на 2 часа

Папка `hydrowatch_solution/` — это распакованный `hydrowatch_solution.zip` **плюс** всё
недостающее по критериям. Скопируйте её содержимое поверх своего репозитория (или пушьте эту
ветку) — всё уже согласовано между собой.

## Разбор по критериям: было → стало

| Критерий (баллы) | Было в zip | Теперь |
|---|---|---|
| Работоспособность сервиса и API (6) | ничего | FastAPI-сервис: `POST /api/analyze` (полигон/bbox + даты или интервалы → подбор пары), отчёт JSON/CSV, GeoJSON 4 слоёв, PNG-оверлеи, GeoTIFF, Swagger `/docs`; 4 теста |
| Карта и визуализация (6) | ничего | `web/` — Leaflet (вендорен), слои «до/пик/прирост/убыль», прозрачность, шторка «до ǀ пик», рисование bbox, выгрузки одной кнопкой |
| Сводный отчёт и выгрузки (6) | ничего | площади га/км² на обе даты, прирост, убыль, нетто, доля от запрошенной территории, разбивка по типам поверхности (WorldCover/GSW/HAND), профиль HAND; JSON + CSV |
| Продуктовое видение / инкрементальное обновление (7) | ничего | `POST /api/observations` (хранится состояние «до», пересчитывается только разность) + раздел 8 отчёта |
| Воспроизводимость (5) | uv.lock, seed; абсолютный путь в metadata; без Docker | `Dockerfile` (2 цели), `docker-compose.yml`, `requirements-service.txt`, путь исправлен, README со структурой данных и всеми командами |
| Критический анализ эталона (5) | 1 абзац | раздел 3 отчёта с цифрами по всем 11 парам (`scripts/reference_audit.py`): 5 пар с дефектным покрытием, JSON≠маска по Зее, flood≠формула |
| Экспериментальная проверка (9) | 3 числа в README | таблица экспериментов + флаг `--ablation no_optical|no_aux` в predict.py для абляций без переобучения (ячейки ⚠️ заполнить) |
| Ресурсоёмкость (3) | нет | таблица в README (сервис измерен; инференс — оценка, `scripts/measure_inference.sh` для замера) |
| Ясность материалов / выступление (8) | нет | `REPORT.md` (8 разделов по рекомендованной структуре), `PRESENTATION.md` (10 слайдов + ответы на вопросы) |

## Таймлайн на 2 часа

**0:00–0:15 — перенести и запустить локально (без Docker).**
```bash
cd hydrowatch_solution
pip install -r requirements-service.txt          # или: uv sync --extra service
PYTHONPATH=src python -m hydrowatch.service.app --data-root /путь/к/hydrowatch_amur
# открыть http://localhost:8000 — карта должна показать Зею-2021 сразу
```
Если набор данных лежит в `data/raw/hydrowatch_amur` внутри проекта — флаг `--data-root` не нужен.

**0:15–0:35 — Docker (первый раз в жизни — это нормально).**
1. Установите Docker Desktop (docker.com → Download), перезагрузитесь, дождитесь «Docker Desktop is running».
2. В терминале в папке `hydrowatch_solution`: `docker compose up --build` (первая сборка 3–5 минут).
3. Откройте http://localhost:8000. Остановить — Ctrl+C или `docker compose down`.
4. Если набор данных не в `data/raw/hydrowatch_amur`:
   `HYDROWATCH_DATA_DIR=/полный/путь/hydrowatch_amur docker compose up --build`
   (Windows PowerShell: `$env:HYDROWATCH_DATA_DIR="C:\...\hydrowatch_amur"; docker compose up --build`).
5. Не получилось за 20 минут — не тратьте время: в README есть путь без Docker, он
   допускается постановкой. Оставьте Dockerfile в репозитории как есть.

**0:35–0:55 — скриншоты и проверка чисел.**
- 3 скриншота для презентации: карта со шторкой «до ǀ пик», панель отчёта, Swagger `/docs`.
- `python scripts/reference_audit.py --root <набор> --submission predictions/submission.csv --output outputs/reference_audit`
  — убедитесь, что Score 0.758 совпадает с отчётом.

**0:55–1:25 — абляции (если есть GPU и выгруженные сцены; иначе пропустить и убрать строки из таблицы 6.2).**
```bash
for a in no_optical no_aux; do
  hydrowatch-predict --root data/raw/hydrowatch_amur --checkpoint weights/best.pt \
    --output-dir outputs/ablation_$a --thresholds 0.95 0.78 0.95 --ablation $a --device cuda
  python scripts/reference_audit.py --root data/raw/hydrowatch_amur \
    --submission outputs/ablation_$a/submission.csv --output outputs/ablation_$a/audit
done
hydrowatch-predict ... --output-dir outputs/ablation_thr05 --thresholds 0.5 0.5 0.5 --device cuda
./scripts/measure_inference.sh cuda   # заполнить таблицу «Ресурсоёмкость» в README
```
Вставьте Score/компоненты в `REPORT.md` → таблица 6.2, и в слайд 5.

**1:25–1:50 — слайды.** Соберите 10 слайдов по `PRESENTATION.md` (Google Slides/PowerPoint),
диаграмму пайплайна возьмите из README (можно скриншотом кода-блока или перерисовать).

**1:50–2:00 — финал.** `ruff check .`, `pytest`, `git add -A && git commit && git push`,
проверьте, что README открывается на GitHub и ссылки на REPORT.md/PRESENTATION.md работают.
Убедитесь, что `weights/best.pt` доступен организаторам (ссылка на облако в README, если
файл не в git).

## Что честно сказать на защите

- Score 0.758 — self-check на выданном эталоне, 8 из 11 пар видны при обучении. Честная
  цифра — отложенная Зея (q_flood 0.53, q_peak 0.57, q_pre 1.00).
- Эталон дефектен на 5 парах (частичное покрытие сцен) — это наш сильный аргумент, он подкреплён
  таблицей; не стесняйтесь показать её.
- Сервис работает на подготовленных масках — это разрешено постановкой; живой конвейер описан
  как сценарий в разделе 8 отчёта, инкрементальное обновление реализовано на уровне API.
