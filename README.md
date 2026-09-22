# HydroWatch Amur — решение КосмоХакатона 2026

Решение задачи гидрологического мониторинга Амурской области по разновременным
Sentinel-1, Sentinel-2 и вспомогательным гидрологическим данным. Модель строит
маски нового затопления, воды до события и воды на пике, после чего рассчитывает
площади в гектарах и формирует `submission.csv`.

## Кратко о решении

- модель: multimodal Siamese U-Net с двумя ResNet-18 энкодерами;
- входы: Sentinel-1 pre/peak, Sentinel-2 pre/peak, рельеф и водные признаки;
- оптический энкодер инициализируется SSL4EO-S12 MoCo-весами;
- обучение ведётся на патчах 384×384 с oversampling редкого класса flood;
- используются отдельные головы `flood`, `water_pre`, `water_peak`;
- loss объединяет focal Tversky, Dice+BCE, ошибку площади и гидрологическую согласованность;
- инференс выполняется скользящим окном с перекрытием и плавным blending;
- Sentinel-сцены выгружаются из публичного STAC Microsoft Planetary Computer,
  поэтому Google Earth Engine и пользовательские ключи не нужны.

## Основные выводы EDA

В наборе 11 пар: восемь паводковых и три контрольных baseline-периода, пять
территорий интереса. Доля нового затопления очень мала: примерно от 0.087% до
1.99% площади AOI. Поэтому обычная равномерная выборка патчей почти не показывает
модели положительный класс; в датасете используется управляемый oversampling
пикселей затопления.

Наиболее устойчивые физические признаки:

- медианный HAND на затопленных пикселях около 0.5 м против 3.7 м на фоне;
- медианный уклон на затопленных пикселях около 0.74° против 1.29° на фоне;
- Sentinel-1 присутствует для всех пар и является основной временной модальностью;
- Sentinel-2 доступен не для всех пар, поэтому используется modality dropout;
- ERA5 на восьми паводковых примерах не показывает устойчивой линейной связи с
  площадью затопления и не используется как жёсткое правило.

Маску flood нельзя безопасно вычислять только как `water_peak & ~water_pre`:
официальная разметка расходится с такой формулой на сотнях тысяч пикселей. Поэтому
flood предсказывается отдельной головой, а логические отношения используются лишь
как мягкая регуляризация и финальная проверка согласованности.

## Исправление покрытия Поярково

Первоначально указанные в `pairs.csv` Sentinel-1 сцены относительной орбиты 105
покрывали только 0.3–0.4% AOI Поярково. Это было обнаружено проверкой nodata,
которой не было в исходных метаданных.

В `configs/scene_overrides.yaml` зафиксирована воспроизводимая замена:

- SAR pre: 2021-04-27, descending orbit 32 — покрытие 100%;
- SAR peak: 2021-06-26, descending orbit 32 — покрытие 100%;
- optical peak: основная дата 2021-06-27, недостающие пиксели заполняются сценой
  2021-06-24 — итоговое покрытие 100%.

Fallback заполняет только nodata основной даты и не усредняет валидные пиксели
между разными датами. Строгий валидатор теперь отклоняет обязательную сцену, если
она покрывает менее 95% эталонной сетки.

## Данные и каналы

| Источник | Каналы |
|---|---|
| Sentinel-1 pre/peak | VV, VH в dB |
| Sentinel-2 pre/peak | B03, B04, B08, B11 |
| AUX | slope, HAND, occurrence, seasonality, max extent, built-up |

Все сцены приводятся к сетке эталонной маски: EPSG:32652, разрешение 10 м. AUX
поставляется в разрешении 30 м и контролируемо ресемплируется во время чтения.

## Архитектура

SAR и optical обрабатываются отдельными ResNet-18. Для каждого масштаба энкодера
строятся temporal-признаки `pre`, `peak`, `abs(peak - pre)`. Затем признаки двух
модальностей объединяются с AUX и индикаторами доступности оптики. U-Net decoder
восстанавливает разрешение патча и возвращает три логита: flood, water pre и water peak.

Оптический ResNet инициализируется SSL4EO-S12 MoCo. Из 13-канального первого слоя
выбираются веса B03, B04, B08 и B11. Веса не скачиваются неявно во время обучения:
файл должен находиться в `weights/`.

## Обучение

Основной конфиг: `configs/train_coverage_fix.yaml`.

- patch size: 384;
- batch size: 4;
- gradient accumulation: 4;
- 4096 патчей на эпоху;
- 65% выборок центрируются на flood;
- вероятность полного optical dropout: 50%;
- AdamW, cosine learning rate schedule и mixed precision;
- validation event: `flood_2021_08_zeya`;
- ранняя остановка по validation loss.

Фиксируется seed 2026. Лучший и последний checkpoints записываются атомарно в
`outputs/<experiment>/best.pt` и `last.pt`; история эпох сохраняется в `metrics.jsonl`.

## Установка

Требуется Python 3.11–3.13. Рекомендуется Python 3.12 и `uv`.

```bash
uv sync --extra dev --extra download --extra analysis
```

## Загрузка Sentinel-сцен

Сначала получить подписанный STAC manifest на машине с доступом к Microsoft
Planetary Computer:

```bash
uv run hydrowatch-download \
  --root data/raw/hydrowatch_amur \
  --sensor all \
  --overwrite \
  --write-manifest outputs/stac_manifest.json
```

Затем скачать и привести COG-файлы к эталонным сеткам:

```bash
uv run hydrowatch-download \
  --root data/raw/hydrowatch_amur \
  --sensor all \
  --manifest outputs/stac_manifest.json
```

`configs/scene_overrides.yaml` применяется автоматически.

## Проверки

```bash
uv run ruff check .
uv run pytest
uv run hydrowatch-validate \
  --require-scenes \
  data/raw/hydrowatch_amur
```

Проверяются структура набора, бинарность разметки, число каналов, CRS, affine
transform, разрешение, совпадение сеток и фактическая доля валидных пикселей сцен.

## Запуск обучения

```bash
uv run hydrowatch-train \
  --config configs/train_coverage_fix.yaml \
  --device cuda
```

Для автономного запуска на GPU-сервере предусмотрен:

```bash
./scripts/run_coverage_fix_training.sh
```

## Инференс и submission

Инференс использует перекрывающиеся патчи и Hann blending, чтобы убрать швы на
границах окон. Flood принудительно не пересекается с `water_pre` и всегда является
подмножеством `water_peak`.

```bash
uv run hydrowatch-predict \
  --root data/raw/hydrowatch_amur \
  --checkpoint outputs/siamese_resnet18_coverage_fix/best.pt \
  --output-dir outputs/final_prediction \
  --thresholds 0.50 0.50 0.50 \
  --patch-size 384 \
  --stride 256 \
  --batch-size 4 \
  --device cuda
```

Команда создаёт:

- `submission.csv` с площадями flood, water pre и water peak;
- `masks/<pair_id>_flood.tif` — требуемые одноканальные uint8-маски;
- `masks/<pair_id>_all.tif` — диагностические трёхканальные маски;
- `prediction_metadata.json` — checkpoint, thresholds и рассчитанные площади.

Порог можно подобрать по вероятностям на полностью отложенном событии:

```bash
uv run hydrowatch-predict \
  --root data/raw/hydrowatch_amur \
  --checkpoint outputs/siamese_resnet18_coverage_fix/best.pt \
  --output-dir outputs/calibration \
  --pair flood_2021_08_zeya__svobodny \
  --save-probabilities \
  --device cuda

uv run hydrowatch-calibrate \
  --root data/raw/hydrowatch_amur \
  --probability-dir outputs/calibration/probabilities \
  --pair flood_2021_08_zeya__svobodny \
  --output outputs/calibration/thresholds.json
```

Калибровочная функция на 70% состоит из Dice и на 30% из согласия площадей. Это
не позволяет оптимизировать только площадь ценой бессмысленной геометрии маски.

## Структура проекта

```text
configs/                 конфиги обучения и overrides сцен
scripts/                 автономные shell-запуски
src/hydrowatch/
  data.py                чтение и patch sampling
  download.py            STAC export и выравнивание сцен
  eda.py                 числовой EDA без графиков
  model.py               multimodal Siamese U-Net
  losses.py              составной loss
  metrics.py             официальная площадная метрика
  train.py               обучение и checkpoints
  predict.py             sliding-window inference
  calibrate.py           калибровка thresholds
  validate.py            строгая проверка данных
tests/                   unit и integration tests
weights/                 локальные pretrained weights
```

## Ограничения

Набор очень мал и содержит автоматически созданную, не полностью вручную
верифицированную разметку. Поэтому качество на новом событии оценивается честным
event-level split, а не случайным разделением патчей. Ни одна метрика на 11 парах
не гарантирует аналогичный результат на скрытом событии 2026 года.

Финальные значения validation loss, thresholds, контрольные суммы весов и архива
добавляются после завершения повторного обучения и сборки submission.
