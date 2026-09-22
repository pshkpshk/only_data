# Пошаговая инструкция (macOS, Apple Silicon) — от нуля до сданного решения

Итоговая картина, к которой идём:

| Куда | Что именно |
|---|---|
| **GitVerse** — репозиторий `hydrowatch-amur` | содержимое папки `hydrowatch_solution/` **в корне репозитория** (README.md, REPORT.md, PRESENTATION.md, src/, web/, configs/, scripts/, tests/, predictions/, Dockerfile, docker-compose.yml, pyproject.toml, uv.lock, requirements-service.txt, SHA256SUMS.txt, docs/) + ссылка на веса в README. **Без** датасета (`data/`), без `outputs/`, без `.venv/` |
| **Яндекс.Диск** (публичная ссылка) | `best.pt` (веса, ~100+ МБ), архив масок `hydrowatch_predictions_masks.zip` |
| **Сайт организаторов — «сабмит»** | файл `predictions/submission.csv` |
| **Сайт — «презентация»** | PDF из 10 слайдов по `PRESENTATION.md` |
| **Сайт — «репозиторий»** | ссылка на GitVerse |
| **Сайт — «сервер»** | ссылка `http://<IP>:8000` на развёрнутый сервис (если успеете, шаг 10) или текст «запуск: `docker compose up`, README §7; демонстрация на защите» |

Всё делается в приложении **Terminal** (Cmd+Space → «Terminal»). Команды копируйте блоками.
Символ `#` и текст после него — комментарий, его вводить не нужно.

---

## Шаг 0. Инструменты (10–15 мин)

```bash
git --version            # если предложит установить Command Line Tools — согласитесь, подождите
curl -LsSf https://astral.sh/uv/install.sh | sh      # менеджер Python-окружений, сам скачает Python 3.12
```
Закройте и снова откройте Terminal, проверьте: `uv --version` → должна напечататься версия.

Docker Desktop (для шага 6, можно ставить параллельно): https://www.docker.com/products/docker-desktop/
→ «Download for Mac — Apple Silicon» → перетащить в Applications → открыть → принять условия →
дождаться, пока кит в строке меню перестанет «бегать». Проверка: `docker compose version`.

## Шаг 1. Забрать файлы решения (5 мин)

```bash
cd ~/Desktop
git clone --branch arena/01a0c8a7-only-data --single-branch https://github.com/pshkpshk/only_data.git only_data_arena
mkdir -p ~/Desktop/hydrowatch-amur
cp -R ~/Desktop/only_data_arena/hydrowatch_solution/. ~/Desktop/hydrowatch-amur/
cd ~/Desktop/hydrowatch-amur
ls
```
Ожидаете увидеть: `Dockerfile PRESENTATION.md README.md REPORT.md SHA256SUMS.txt configs docker-compose.yml
docs predictions pyproject.toml requirements-service.txt scripts src tests uv.lock web`.

`~/Desktop/hydrowatch-amur` — это и есть будущий репозиторий на GitVerse. Дальше работаем только в нём.

## Шаг 2. Положить датасет и веса (5 мин)

Путь к папке проще всего получить, перетащив её из Finder в окно Terminal.

```bash
mkdir -p data/raw weights
ln -s "/ПОЛНЫЙ/ПУТЬ/К/hydrowatch_amur" data/raw/hydrowatch_amur    # символическая ссылка, копировать не надо
cp "/ПОЛНЫЙ/ПУТЬ/К/best.pt" weights/best.pt
ls -lh weights/best.pt                                              # запомните размер (нужно в шаге 8)
shasum -a 256 weights/best.pt                                       # должно начинаться с 6c7ef209 и заканчиваться 2d06
ls data/raw/hydrowatch_amur/                                        # pairs.csv rasters reference_masks sample_submission.csv tables ...
```
Где взять `best.pt`: в архиве `hydrowatch_solution_final.tar.gz` (внутри `hydrowatch_solution/weights/best.pt`)
или на обучающей машине `outputs/siamese_resnet18_coverage_finetune/best.pt`.

Есть ли выгруженные снимки (нужны только для повторного инференса, не для сервиса):
```bash
ls data/raw/hydrowatch_amur/rasters/flood_2021_08_zeya/svobodny/
```
Если видите `S1_pre.tif`, `S1_peak.tif` (или `S1_pre_2021-06-26.tif`) — снимки есть, шаг 7 доступен.
Если только `.json`, `.csv` и `AUX_terrain_gsw.tif` — снимков нет, шаг 7 пропускаем (это нормально).

## Шаг 3. Установить окружение и запустить сервис (10–15 мин)

```bash
uv sync --extra service --extra dev        # 3–7 минут: скачает Python 3.12, torch, rasterio…; обновит uv.lock — это нормально
uv run pytest -q                           # ожидается: 22 passed
uv run ruff check .                        # ожидается: All checks passed!
uv run hydrowatch-service --data-root data/raw/hydrowatch_amur --port 8000
```
Когда появится `Uvicorn running on http://0.0.0.0:8000` — откройте в браузере **http://localhost:8000**.
Должна загрузиться карта с Зеей‑2021 (красный прирост, голубая вода), справа — отчёт.

Проверьте руками (это ровно то, что будете показывать на защите):
1. Выпадающий список «Готовая пара» → выбрать другую пару → карта пересчиталась.
2. Галочка «Режим сравнения „до | пик“» → появилась шторка, тяните её мышью.
3. Кнопка «нарисовать bbox» → протянуть прямоугольник на карте → «Рассчитать» → отчёт по кусочку.
4. В блоке «Выгрузки» нажать «GeoJSON: Прирост (затопление)» и «Отчёт CSV» — файлы скачаются.
5. Открыть **http://localhost:8000/docs** — Swagger со всеми эндпоинтами.

Сделайте 3 скриншота (Cmd+Shift+4, выделить область; файлы падают на Рабочий стол) и переложите:
```bash
mv ~/Desktop/Снимок*1*.png docs/screenshots/01_map_swipe.png       # имена файлов подставьте свои
mv ~/Desktop/Снимок*2*.png docs/screenshots/02_report_panel.png
mv ~/Desktop/Снимок*3*.png docs/screenshots/03_swagger.png
```
Остановить сервис: в Terminal нажать **Ctrl+C**.

Если что‑то не запустилось — скопируйте последние 20 строк из Terminal и пришлите мне.

## Шаг 4. Проверить, что submission валиден (2 мин)

```bash
uv run hydrowatch-check-submission --root data/raw/hydrowatch_amur \
  --submission predictions/submission.csv --mask-dir predictions/masks
# ожидается: submission validation: OK
uv run python scripts/reference_audit.py --root data/raw/hydrowatch_amur \
  --submission predictions/submission.csv --output outputs/reference_audit
# внизу вывода: Score = 0.7584
```

## Шаг 5. Веса на Яндекс.Диск и ссылка в README (5 мин)

Файл больше 100 МБ в GitVerse по HTTPS не зальётся, поэтому веса отдаём ссылкой.
1. disk.yandex.ru → «Загрузить» → `weights/best.pt` → после загрузки «Поделиться» → «Скопировать ссылку».
2. Вставить ссылку в README (замените `https://disk.yandex.ru/d/XXXX` на свою):
```bash
sed -i '' 's#<ССЫЛКА НА ЯНДЕКС.ДИСК>#https://disk.yandex.ru/d/XXXX#' README.md
grep -n "disk.yandex" README.md          # убедиться, что ссылка встала
```

## Шаг 6. Docker (15–20 мин, очень желательно — это критерий «Воспроизводимость»)

Docker Desktop должен быть запущен (кит в строке меню).
```bash
HYDROWATCH_DATA_DIR="/ПОЛНЫЙ/ПУТЬ/К/hydrowatch_amur" docker compose up --build
```
Первая сборка 3–6 минут (качает образ Python и библиотеки). Успех — те же строки
`Uvicorn running on http://0.0.0.0:8000`, и http://localhost:8000 снова открывается.
Остановить: Ctrl+C, затем `docker compose down`.

Не собралось за 20 минут — не тратьте время: путь без Docker уже описан в README и допускается
постановкой. Dockerfile оставьте в репозитории. Ошибку пришлите мне — скорее всего, правится одной строкой.

## Шаг 7. (Опционально, только если снимки есть) инференс на Apple GPU и абляция (фон, 30–60 мин)

Пока делаете слайды, в **отдельной вкладке Terminal** (Cmd+T) запустите:
```bash
cd ~/Desktop/hydrowatch-amur
uv run hydrowatch-predict --root data/raw/hydrowatch_amur --checkpoint weights/best.pt \
  --output-dir outputs/ablation_no_optical --thresholds 0.95 0.78 0.95 \
  --patch-size 384 --stride 256 --batch-size 4 --device mps --ablation no_optical \
&& uv run python scripts/reference_audit.py --root data/raw/hydrowatch_amur \
  --submission outputs/ablation_no_optical/submission.csv --output outputs/ablation_no_optical/audit
```
Когда закончится, в конце вывода будет `Score = …` и компоненты. Впишите их в `REPORT.md`,
таблица 6.2, строка «Без оптики» (откройте файл в TextEdit/VS Code, замените `⚠️ ЗАПОЛНИТЬ | | | | | |`
на числа `0.xx | 0.xx | 0.xx | 1.00 | 0.xx | 0.xx`). Аналогично `--ablation no_aux` и
`--thresholds 0.5 0.5 0.5` (без `--ablation`), если время позволяет.

Что не успели — удаляем одной командой (она убирает только строки с пометкой):
```bash
grep -v "ЗАПОЛНИТЬ" REPORT.md > /tmp/r.md && mv /tmp/r.md REPORT.md
grep -c "ЗАПОЛНИТЬ" REPORT.md            # должно быть 0
```
**Важно:** `predictions/` не трогаем — там результат с CUDA, который сходится с масками и SHA‑256.

## Шаг 8. Презентация (25 мин)

Google Slides или Keynote, 10 слайдов строго по таблице в `PRESENTATION.md` (там же — ответы на
вопросы). Схему пайплайна — скриншот блока из README §1. Скриншоты сервиса — из `docs/screenshots/`.
Экспорт: Файл → Скачать/Экспорт → **PDF** → сохранить как `~/Desktop/hydrowatch-amur/docs/presentation.pdf`.

Отчёт: `REPORT.md` остаётся в репозитории как есть (организаторы читают Markdown на GitVerse);
если форма требует PDF — откройте REPORT.md на GitVerse после пуша → Cmd+P → «Сохранить как PDF».

## Шаг 9. Залить на GitVerse (10 мин)

1. https://gitverse.ru → войти/зарегистрироваться → «Создать репозиторий»: имя `hydrowatch-amur`,
   видимость **публичный** (организаторам не нужны доступы), **не** ставить галочки «добавить README/.gitignore».
2. Токен: аватар → Настройки → «Токены доступа» → создать (права на чтение/запись репозиториев) →
   скопировать (показывается один раз).
3. В Terminal (подставьте свой логин GitVerse вместо `ЛОГИН`):
```bash
cd ~/Desktop/hydrowatch-amur
grep -v "ЗАПОЛНИТЬ" REPORT.md > /tmp/r.md && mv /tmp/r.md REPORT.md      # на всякий случай, если пропустили шаг 7
find . -type f ! -name SHA256SUMS.txt ! -path './.git/*' ! -path './data/*' ! -path './outputs/*' \
  ! -path './.venv/*' ! -path './weights/*' ! -name '.DS_Store' ! -path '*/__pycache__/*' \
  ! -path './.pytest_cache/*' ! -path './.ruff_cache/*' -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS.txt
git init -b main
git add -A
git status | head -40        # проверка: НЕ должно быть data/, weights/, outputs/, .venv/; должны быть predictions/masks/*.tif
git commit -m "HydroWatch Amur: multimodal flood segmentation, inference, service, report"
git remote add origin https://gitverse.ru/ЛОГИН/hydrowatch-amur.git
git push -u origin main      # логин = ваш логин GitVerse, пароль = токен из п.2
```
4. Откройте страницу репозитория в браузере: README должен отрисоваться, ссылки на REPORT.md и
   PRESENTATION.md — открываться, папка `predictions/masks` — содержать 22 tif‑файла.

Если push отклонён из‑за размера файла — значит в индекс попало что‑то лишнее: `git status`, найдите
большой файл, добавьте его путь в `.gitignore`, затем `git rm --cached <файл>`, `git commit -m fix`, `git push`.

## Шаг 10. Поле «сервер» (опционально, 20 мин; иначе — текст)

Скорее всего, это ссылка на работающий прототип. Самый простой способ — арендовать на неделю VPS
(Timeweb Cloud / Selectel / Yandex Cloud: Ubuntu 22.04, 2 vCPU, 4 ГБ, ~300–600 ₽) и на нём:
```bash
ssh root@IP
apt update && apt install -y docker.io docker-compose-v2 git
git clone https://gitverse.ru/ЛОГИН/hydrowatch-amur.git && cd hydrowatch-amur
docker compose up -d --build            # без датасета: сервис работает, разбивка по покрову помечена «недоступна»
```
Чтобы была и разбивка по покрову, докиньте только AUX‑растры (67 МБ) с Mac:
`scp -r "/ПУТЬ/К/hydrowatch_amur" root@IP:/root/hydrowatch-amur/data/raw/` (можно только `pairs.csv` и `rasters/`).
Ссылка в форму: `http://IP:8000` (Swagger — `http://IP:8000/docs`). Порт 8000 должен быть открыт в
файрволе панели хостинга.

Нет времени — в поле пишем: «Сервис запускается командой `docker compose up` (README, раздел 7);
демонстрация на защите». Если у организаторов «сервер» означает что‑то другое — уточните в их чате.

## Шаг 11. Сабмит на сайте (10 мин)

1. **Сабмит** — файл `~/Desktop/hydrowatch-amur/predictions/submission.csv` (11 строк + заголовок, разделитель запятая).
2. **Маски** («передаются отдельно») — собрать архив ровно в требуемой структуре и выложить на Яндекс.Диск:
```bash
cd ~/Desktop/hydrowatch-amur
rm -rf /tmp/pack && mkdir -p /tmp/pack/predictions
cp predictions/masks/*_flood.tif /tmp/pack/predictions/          # только 11 файлов <pair_id>_flood.tif
cp predictions/submission.csv /tmp/pack/predictions/
(cd /tmp/pack && zip -r ~/Desktop/hydrowatch_predictions_masks.zip predictions)
ls -lh ~/Desktop/hydrowatch_predictions_masks.zip
```
   Ссылку на архив укажите там, где организаторы просят маски (форма/чат/почта), и продублируйте
   в README (раздел 4 или 5) одной строкой «Маски сабмита: <ссылка>».
3. **Презентация** — `docs/presentation.pdf`.
4. **Репозиторий** — `https://gitverse.ru/ЛОГИН/hydrowatch-amur`.
5. **Сервер** — из шага 10.

После правок README (ссылки на маски/сервер) — `git add -A && git commit -m "links" && git push`.

## Финальный чек‑лист (2 мин)

- [ ] На GitVerse открывается README, в нём рабочая ссылка на `best.pt` и SHA‑256.
- [ ] В репозитории нет `data/`, `outputs/`, `.venv/`; есть `predictions/masks/*_flood.tif`.
- [ ] `submission.csv` загружен; архив масок доступен по ссылке.
- [ ] В REPORT.md нет слова «ЗАПОЛНИТЬ» (`grep -c ЗАПОЛНИТЬ REPORT.md` → 0).
- [ ] PDF презентации загружен; в нём есть скриншоты сервиса и таблица метрики.
- [ ] Ссылки на Яндекс.Диск открываются в режиме инкогнито (доступ «по ссылке»).
- [ ] Docker Desktop / VPS: `docker compose up` показывал карту хотя бы один раз (или честно указано «локальный запуск»).
