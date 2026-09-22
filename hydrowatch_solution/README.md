# HydroWatch Amur — решение КосмоХакатона 2026

Оперативный мониторинг гидрологической динамики Амурской области по совместным данным
Sentinel-1 (SAR) и Sentinel-2 (MSI). Решение строит маски воды на дату «до» и на дату
пика, выделяет зону нового затопления и убыль, считает площади и отдаёт результат через
REST API и интерактивную карту.

Комплект: **код обучения и инференса**, **сервис (API + карта + выгрузки + отчёт)**,
**submission.csv и маски**, **отчёт** ([REPORT.md](REPORT.md)), **презентация**
([PRESENTATION.md](PRESENTATION.md)).

---

## 1. Схема пайплайна

```
                 ┌──────────────────────── ВХОД (пара «до / пик») ───────────────────────┐
                 │ S1 pre/peak (VV,VH дБ)   S2 pre/peak (B03,B04,B08,B11)   AUX (slope,  │
                 │ обязательно              опционально, может отсутствовать  HAND, GSW,   │
                 │                                                            builtup)     │
                 └───────────┬───────────────────────┬──────────────────────────┬─────────┘
                             ▼                       ▼                          │
                    ResNet-18 (SAR)          ResNet-18 (optical, SSL4EO-S12)    │
                    признаки pre/peak/|Δ|    признаки pre/peak/|Δ| × флаг        │
                             └───────────┬───────────┘  доступности оптики      │
                                         ▼                                      │
                       U-Net decoder ◄── конкатенация + AUX ◄───────────────────┘
                                         │
                 три головы: flood · water_pre · water_peak  (sigmoid, sliding window 384/256, Hann)
                                         │
              пороги 0.95 / 0.78 / 0.95 → логика: flood ⊂ water_peak, flood ∩ water_pre = ∅
                                         │
        ┌────────────────────────────────┼─────────────────────────────────────────┐
        ▼                                ▼                                         ▼
 submission.csv                 masks/<pair>_flood.tif (uint8 0/1)         masks/<pair>_all.tif
 (flood/pre/peak, га)           masks/<pair>_all.tif (3 канала)            ──► СЕРВИС (раздел 5)
                                                                             карта · GeoJSON · отчёт · API
```

Обоснование схемы, эксперименты и критический разбор эталона — в [REPORT.md](REPORT.md).

## 2. Кратко о решении

- модель: multimodal Siamese U-Net с двумя ResNet-18 энкодерами (SAR и оптика);
- SAR — основная всепогодная модальность, присутствует у всех пар; оптика — опциональная
  ветвь с modality dropout (50 % при обучении), поэтому пары без пригодной оптики
  обрабатываются тем же весом без падения;
- AUX-слои (уклон, HAND, JRC GSW occurrence/seasonality/max_extent, застройка) подаются в
  декодер как гидрологический контекст;
- отдельные головы `flood`, `water_pre`, `water_peak`; loss = focal Tversky + Dice/BCE +
  ошибка площади + штраф за нарушение гидрологической логики;
- обучение на патчах 384×384 с oversampling класса flood (65 % патчей центрируются на затоплении);
- валидация — по событию (`flood_2021_08_zeya` полностью отложено), seed 2026;
- инференс скользящим окном с перекрытием и Hann-blending — растр не грузится в GPU целиком;
- сцены Sentinel выгружаются из STAC Microsoft Planetary Computer (не требует GEE-ключей);
  инференс и сервис работают полностью офлайн по уже выгруженным сценам/маскам.

Метрика self-check на выданном эталоне: **Score 0.758** (Q_flood 0.699, Q_peak 0.768,
Q_pre 0.680, Spec_base 1.0) — см. `scripts/reference_audit.py`; честная оценка
на отложенном событии — в отчёте.

## 3. Требования и установка

Python 3.11–3.13 (рекомендуется 3.12), Linux/macOS/Windows. GPU нужна только для обучения;
инференс работает и на CPU (медленнее). Все зависимости зафиксированы в `uv.lock`
(полный стек) и `requirements-service.txt` (только сервис).

```bash
# вариант A: uv (рекомендуется); эквивалент: uv sync --all-extras
uv sync --extra dev --extra service --extra analysis --extra download

# вариант B: pip
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,service,analysis,download]"
```

Docker (ничего, кроме Docker Desktop / docker engine, ставить не нужно):

```bash
docker compose up --build                                  # сервис: http://localhost:8000
docker compose --profile inference run --rm inference      # пересчёт масок и submission.csv (CPU)
```

## 4. Данные: ожидаемая структура каталогов

```
data/raw/hydrowatch_amur/            # набор организаторов как выдан (+ выгруженные сцены)
  pairs.csv                          # перечень пар, даты, орбиты, пути
  sample_submission.csv
  rasters/<event_id>/<aoi_id>/
    AUX_terrain_gsw.tif              # 6 каналов, 30 м (ресемплируется на сетку маски при чтении)
    S1_pre.tif / S1_peak.tif         # выгрузка: VV, VH (дБ), сетка эталонной маски  ← hydrowatch-download
    SENTINEL2_pre.tif / _peak.tif    # выгрузка: B03,B04,B08,B11 (SR ×10000)         ← hydrowatch-download
    ERA5_daily_*.csv, *.json         # как выдано
  reference_masks/reference_<pair_id>.tif|json
weights/                             # не хранится в git — см. ссылку ниже
  best.pt                            # финальный checkpoint (SHA-256 6c7ef209…2d06)
  resnet18_sentinel2_all_moco.pth    # SSL4EO-S12 MoCo (только для обучения с нуля)
predictions/                         # результат инференса, который сдаётся
  submission.csv
  masks/<pair_id>_flood.tif          # uint8 0/1, сетка растров пары   ← требование сабмита
  masks/<pair_id>_all.tif            # flood / water_pre / water_peak — источник данных сервиса
  prediction_metadata.json, thresholds.json
```

**Веса модели:** `weights/best.pt` (≈ размер см. по ссылке) — скачать: **<ССЫЛКА НА ЯНДЕКС.ДИСК>**,
положить в `weights/`, проверить `shasum -a 256 weights/best.pt` →
`6c7ef209ffe3f470d862ebeb2b4770d8d480406fd81a47d9bf7254323b8c2d06`. Доступ по ссылке сохраняется
не менее 10 рабочих дней после окончания соревнования.

**Маски сабмита** (`predictions/<pair_id>_flood.tif`, 11 файлов) лежат в `predictions/masks/` этого
репозитория и продублированы архивом в требуемой структуре: **<ССЫЛКА НА АРХИВ МАСОК>**.

Пути нигде не зашиты: всё задаётся аргументами CLI, `configs/*.yaml` и переменными окружения.

### Выгрузка сцен Sentinel (один раз, нужен интернет)

```bash
uv run hydrowatch-download --root data/raw/hydrowatch_amur --sensor all --overwrite \
    --write-manifest outputs/stac_manifest.json          # 1) подписанный STAC-манифест
uv run hydrowatch-download --root data/raw/hydrowatch_amur --sensor all \
    --manifest outputs/stac_manifest.json                # 2) скачивание и приведение к сетке масок
uv run hydrowatch-validate --require-scenes data/raw/hydrowatch_amur   # строгая проверка покрытия
```

`configs/scene_overrides.yaml` применяется автоматически (замена сцен орбиты 105 для Поярково,
покрывавших 0.3–0.4 % AOI, на орбиту 32 с покрытием 100 % — подробности в отчёте).

## 5. Инференс: где появляется submission.csv

```bash
uv run hydrowatch-predict \
  --root data/raw/hydrowatch_amur \
  --checkpoint weights/best.pt \
  --output-dir outputs/final_prediction \
  --thresholds 0.95 0.78 0.95 --patch-size 384 --stride 256 --batch-size 4 \
  --device auto                       # cuda | cpu | auto
```

Результат: `outputs/final_prediction/submission.csv`, `masks/<pair_id>_flood.tif`,
`masks/<pair_id>_all.tif`, `prediction_metadata.json`. Проверка формата и сходимости площадей
с масками:

```bash
uv run hydrowatch-check-submission --root data/raw/hydrowatch_amur \
  --submission outputs/final_prediction/submission.csv --mask-dir outputs/final_prediction/masks
```

Абляции для отчёта тем же checkpoint'ом (без переобучения):
`--ablation no_optical` (оптика выключена, флаг доступности = 0) и `--ablation no_aux`.

Пороги, коэффициенты и конфигурации (обучаемых компонентов кроме весов нет):
`predictions/thresholds.json` (0.95 / 0.78 / 0.95, калибровка на отложенной Зее, 70 % Dice +
30 % сходимость площади), `configs/train*.yaml`, `configs/service.yaml`.

Детерминированность: seed 2026 во всех библиотеках; инференс без случайности; повторный
запуск даёт идентичные маски (сверка по SHA-256 в `SHA256SUMS.txt`).

## 6. Обучение

```bash
uv run hydrowatch-train --config configs/train_coverage_fix.yaml --device cuda        # с нуля
uv run hydrowatch-train --config configs/train_coverage_finetune.yaml --device cuda   # финальный fine-tune
./scripts/run_coverage_fix_training.sh                                                # автономно на сервере
```

Финальный checkpoint = консервативное дообучение (6 эпох, lr 1e-5) лучшей модели на исправленных
сценах; validation loss на отложенной Зее 0.489 → 0.449 (−8.3 %). Полное переобучение с нуля дало
0.614 и в финал не вошло. Веса SSL4EO-S12 (MoCo, ResNet-18, лицензия Apache-2.0,
https://github.com/zhu-xlab/SSL4EO-S12) кладутся в `weights/` вручную; в обучении используются
каналы B03/B04/B08/B11 из 13-канального первого слоя.

## 7. Сервис: карта, выгрузки, отчёт, REST API

Сервис работает на заранее подготовленном наборе сцен (маски `predictions/masks/*_all.tif`) —
это допускается постановкой; все числа в отчётах воспроизводятся подсчётом пикселей по этим
маскам и совпадают с `submission.csv` при запросе на весь район.

```bash
# Docker (рекомендуется для проверки)
docker compose up --build                 # → http://localhost:8000   (Swagger: /docs)
# HYDROWATCH_DATA_DIR=/путь/к/hydrowatch_amur docker compose up --build   # если набор лежит не в data/raw

# без Docker
uv run hydrowatch-service --data-root data/raw/hydrowatch_amur --port 8000
# или: PYTHONPATH=src python -m hydrowatch.service.app --data-root data/raw/hydrowatch_amur
```

Набор организаторов сервису нужен только ради `AUX_terrain_gsw.tif` (разбивка затопления по
типам поверхности, профиль HAND); без него сервис стартует, а в отчёте помечает разбивку как
недоступную.

### Что умеет

| Требование постановки | Реализация |
|---|---|
| Пространственно-временной запрос | `POST /api/analyze` — GeoJSON-полигон **или** bbox (WGS84) + даты «до»/«после» (точки с допуском ±20 дней или интервалы); сервис подбирает подходящую подготовленную пару по пересечению и датам, при отсутствии — возвращает список доступных пар для этой территории |
| Интерактивная карта | Leaflet (вендорен локально): слои «вода до», «вода пик», «прирост», «убыль» поверх OSM/Esri-подложки; переключение, прозрачность, режим сравнения шторкой «до ǀ пик»; рисование bbox мышью. Изменение дат в форме переводит запрос в режим «территория + даты»: сервис подбирает подготовленную пару (±20 дней) и переключает карту на неё, иначе показывает список доступных пар для территории |
| Векторные контуры | `GET …/vectors/{water_pre,water_peak,flood,receded}.geojson` — EPSG:4326 (CRS84), атрибуты `id, type, area_ha, area_km2, pair_id, date_pre, date_peak`; MMU 0.25 га (25 px, как у эталона), упрощение 5 м |
| Сводный отчёт | JSON и CSV: площадь зеркала на каждую дату (га и км²), прирост и убыль, нетто-изменение, доля затопления от запрошенной территории, разбивка затопления по типам поверхности (застройка WorldCover / постоянная и сезонная вода GSW / пойма / низкие / возвышенные земли по HAND), профиль HAND |
| REST API без UI | все функции доступны как HTTP-эндпоинты, документация OpenAPI на `/docs` |
| Инкрементальное обновление | `POST /api/observations` — новая маска воды по AOI сравнивается с хранимым состоянием «до»; пересчитываются только разностные слои, сцена и сводка сохраняются в `state_dir` (сценарий описан в REPORT.md, раздел 7) |
| Растровая маска | `GET /api/pairs/{pair_id}/mask.tif?layer=flood` — GeoTIFF в сетке пары |

### Примеры API

```bash
curl localhost:8000/api/pairs                                     # каталог пар с footprint'ами
curl -X POST localhost:8000/api/analyze -H 'Content-Type: application/json' -d '{
  "bbox": [128.0, 51.3, 128.2, 51.45],
  "date_pre": "2021-06-20", "date_peak": "2021-08-15"}'          # → отчёт + job_id + ссылки
curl localhost:8000/api/jobs/<job_id>/vectors/flood.geojson -o flood.geojson
curl localhost:8000/api/jobs/<job_id>/report.csv -o report.csv
curl localhost:8000/api/pairs/flood_2021_08_zeya__svobodny/report.json   # весь район сразу
curl -X POST localhost:8000/api/observations -F aoi_id=svobodny -F observed_on=2021-08-25 \
  -F baseline_pair_id=flood_2021_08_zeya__svobodny -F water_mask=@new_scene_water.tif
```

Параметры сервиса — `configs/service.yaml` (MMU, допуск дат, порог постоянной воды, классы
разбивки), переменные окружения `HYDROWATCH_PREDICTIONS_DIR`, `HYDROWATCH_DATA_ROOT`,
`HYDROWATCH_PAIRS_CSV`, `HYDROWATCH_STATE_DIR`.

## 8. Ресурсоёмкость и время

| Компонент | Память | Время | Условия |
|---|---|---|---|
| Инференс, GPU | ≈ 2–3 ГБ VRAM (AMP, batch 4, патч 384) | ≈ 10–20 с на пару, ≈ 3 мин на 11 пар | одна потребительская GPU (уровень RTX 3060) — оценка по конфигурации; измерить: `scripts/measure_inference.sh` |
| Инференс, CPU | ≈ 4–6 ГБ RAM | ≈ 2–4 мин на пару | 8 потоков — оценка |
| Сервис | ≈ 0.3 ГБ на старте, до ≈ 0.6 ГБ с кэшем 3 пар (маски bool + компактный AUX) | отчёт по паре 0.6–1.1 с; GeoJSON 0.4–1.0 с; PNG-оверлей 0.2–0.3 с | 2 vCPU, измерено на 11 парах набора |

Растры обрабатываются окнами 384×384 (stride 256); полностью в память загружаются только входные
каналы одной пары (≈ 0.5 ГБ float32 на 4000×4000). Измерить пиковую память/время:
`./scripts/measure_inference.sh` (использует `/usr/bin/time -v`).

## 9. Проверки

```bash
uv run ruff check .
uv run pytest                        # 22 теста при установке всех extras; тесты extras
                                     # analysis/download/service пропускаются, если extra не установлен
uv run hydrowatch-validate --require-scenes data/raw/hydrowatch_amur
python scripts/reference_audit.py --root data/raw/hydrowatch_amur \
   --submission predictions/submission.csv --output outputs/reference_audit   # метрика + аудит эталона
```

## 10. Структура проекта

```text
configs/                 train*.yaml (обучение), scene_overrides.yaml (сцены), service.yaml (сервис)
scripts/                 run_*.sh (обучение/финализация), reference_audit.py, measure_inference.sh,
                         package_solution.sh
src/hydrowatch/
  data.py                чтение растров, patch sampling, нормализация
  download.py            STAC-выгрузка и выравнивание сцен на сетку масок
  eda.py                 числовой EDA
  model.py               multimodal Siamese U-Net
  losses.py              составной loss
  train.py               обучение, checkpoints, seed
  predict.py             sliding-window инференс, абляции, submission
  calibrate.py           калибровка порогов
  metrics.py             официальная площадная метрика
  submission.py          валидатор сабмита
  validate.py            строгая проверка набора
  service/               config.py · catalog.py · analysis.py (площади, GeoJSON, PNG) · app.py (FastAPI)
web/                     index.html, app.js, app.css, vendor/leaflet (BSD-2)
tests/                   unit/integration тесты (+ tests/test_service.py)
predictions/             сданные submission.csv и маски
Dockerfile, docker-compose.yml, requirements-service.txt, uv.lock, pyproject.toml
REPORT.md, PRESENTATION.md
```

## 11. Ограничения и честные оговорки

- Набор мал (11 пар, 5 районов), эталон построен автоматически и содержит дефекты (частичное
  покрытие сцен орбиты 105, расхождение JSON-статистики и маски по Зее) — см. REPORT.md, раздел 3.
- Оценка на выданном эталоне не является оценкой обобщения: часть пар участвовала в обучении;
  честная цифра — отложенное событие `flood_2021_08_zeya`.
- Сервис работает на подготовленных сценах; подключение живого конвейера выгрузки описано как
  сценарий (REPORT.md, раздел 7), но не входит в прототип.

## Лицензии

Код — MIT. Данные Copernicus Sentinel-1/2 — открытая лицензия Copernicus; JRC GSW, MERIT Hydro,
HydroSHEDS, ESA WorldCover, OSM — по лицензиям правообладателей. SSL4EO-S12 — Apache-2.0.
Leaflet — BSD-2-Clause (`web/vendor/LEAFLET_LICENSE.txt`). Подложки карты (OSM, Esri) — по
условиям поставщиков тайлов, только для демонстрации.
