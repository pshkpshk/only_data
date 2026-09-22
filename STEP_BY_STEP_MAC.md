# Пошаговая инструкция (macOS, Apple Silicon) — каждый маленький шаг

Итоговая картина, к которой идём:

| Куда | Что именно |
|---|---|
| **GitVerse** — репозиторий `hydrowatch-amur` | содержимое папки `hydrowatch_solution/` **в корне репозитория** (README.md, REPORT.md, PRESENTATION.md, src/, web/, configs/, scripts/, tests/, predictions/, Dockerfile, docker-compose.yml, pyproject.toml, uv.lock, requirements-service.txt, SHA256SUMS.txt, docs/) + ссылки на веса и архив масок в README. **Без** датасета (`data/`), без `outputs/`, `.venv/`, `weights/` |
| **Яндекс.Диск** (публичные ссылки) | `best.pt` (веса), архив масок `hydrowatch_predictions_masks.zip` |
| **Сайт организаторов — «сабмит»** | файл `predictions/submission.csv` |
| **Сайт — «презентация»** | PDF из 10 слайдов по `PRESENTATION.md` (`docs/presentation.pdf`) |
| **Сайт — «репозиторий»** | ссылка на GitVerse |
| **Сайт — «сервер»** | `http://IP:8000` развёрнутого сервиса (шаг 10А) или текст «запуск `docker compose up`, README §7; демонстрация на защите» (10Б) |

## Как читать инструкцию

- Блоки `code` — команды. Скопировать (Cmd+C) → вставить в Terminal (Cmd+V) → **Enter**. Одна строка = одна команда. Если строка заканчивается `\`, она продолжается на следующей — такой блок копируется целиком.
- Если после команды ничего не напечаталось и снова появилось приглашение вида `имя@MacBook папка %` — команда выполнена успешно.
- Остановить то, что «висит» в Terminal — **Control+C** (клавиша Control, не Cmd).
- Пароли и токены при вводе не отображаются — вставляйте «вслепую» и жмите Enter.
- `~` — ваша домашняя папка (`/Users/имя`), `~/Desktop` — Рабочий стол.
- Путь к папке/файлу проще всего получить, **перетащив** объект из Finder в окно Terminal — путь вставится сам, кавычки не нужны.
- Слова `ЛОГИН`, `ВАШЕ_ИМЯ`, `IP_АДРЕС`, `XXXX` — заменяйте на свои.

---

## Шаг 0. Инструменты (10–15 мин)

### 0.1 Открыть Terminal
Cmd+Space → набрать `Terminal` → Enter. Появится окно с приглашением `имя@MacBook-Pro ~ %`.

### 0.2 Git
```bash
git --version
```
- Появилось окно «Для команды git требуются инструменты разработчика командной строки» → **Установить** → **Принимаю** → подождать 5–10 минут → **Готово** → снова `git --version`.
- Ожидаемый ответ: `git version 2.39.x (Apple Git-…)` или новее.

### 0.3 uv (менеджер Python-окружений)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
10–30 секунд, в конце `everything's installed!`. Закройте Terminal полностью (Cmd+Q), откройте заново (0.1) и проверьте:
```bash
uv --version
```
→ `uv 0.x.y`. Если `zsh: command not found: uv`:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc && uv --version
```

### 0.4 Docker Desktop (нужен к шагу 6; можно ставить параллельно с шагами 1–5)
1. Браузер → https://www.docker.com/products/docker-desktop/ → **Download for Mac — Apple Silicon** (не Intel chip).
2. В Загрузках `Docker.dmg` → двойной клик → перетащить значок **Docker** на **Applications**.
3. Finder → Программы → **Docker** → двойной клик → «приложение загружено из интернета, открыть?» → **Открыть**.
4. **Docker Subscription Service Agreement** → **Accept** → **Use recommended settings** → **Finish** → пароль от Mac, если попросит.
5. Предложение войти → **Skip** («Continue without signing in»); опрос → **Skip survey**.
6. В строке меню появится кит. Пока он анимируется — Docker стартует (1–2 мин). Когда внизу слева окна Docker написано **Engine running** — готово.
7. Проверка:
```bash
docker compose version
```
→ `Docker Compose version v2.xx.x`. Если `command not found: docker` — Docker Desktop ещё ни разу не был открыт (п. 3).

---

## Шаг 1. Забрать файлы решения (5 мин)

```bash
cd ~/Desktop
```
Приглашение станет `… Desktop %`.
```bash
git clone --branch arena/01a0c8a7-only-data --single-branch https://github.com/pshkpshk/only_data.git only_data_arena
```
Печатает `Cloning into 'only_data_arena'...` → `Receiving objects: 100%` → `Resolving deltas: 100%`. 10–60 секунд. На Рабочем столе появится папка `only_data_arena` (логин GitHub спрашивать не должен — репозиторий публичный).
```bash
mkdir -p ~/Desktop/hydrowatch-amur
```
Создаёт пустую папку — будущий репозиторий. Ничего не печатает.
```bash
cp -R ~/Desktop/only_data_arena/hydrowatch_solution/. ~/Desktop/hydrowatch-amur/
```
Копирует содержимое (точка после `solution/` обязательна — «содержимое папки, а не сама папка»). Ничего не печатает.
```bash
cd ~/Desktop/hydrowatch-amur
ls
```
Ожидаемый вывод (порядок может отличаться):
```
Dockerfile   PRESENTATION.md   README.md   REPORT.md   SHA256SUMS.txt   configs   docker-compose.yml
docs   predictions   pyproject.toml   requirements-service.txt   scripts   src   tests   uv.lock   web
```
```bash
ls -a | grep -E "gitignore|dockerignore"
```
→ `.dockerignore` и `.gitignore` (скрытые файлы тоже скопировались).

**Дальше все команды выполняются из папки `~/Desktop/hydrowatch-amur`.** В каждой новой вкладке/окне Terminal сначала `cd ~/Desktop/hydrowatch-amur`.

---

## Шаг 2. Датасет и веса (5 мин)

### 2.1 Найти датасет в Finder
Нужна папка, внутри которой лежат `pairs.csv`, `sample_submission.csv`, папки `rasters`, `reference_masks`, `tables`. Обычно она называется `hydrowatch_amur`. Если есть только `hydrowatch_amur.zip` — двойной клик в Finder распакует. Если внутри распакованной папки лежит ещё одна `hydrowatch_amur` — нужна внутренняя (та, где `pairs.csv`).

### 2.2 Подключить датасет (ярлыком, без копирования)
```bash
mkdir -p data/raw weights
```
Наберите `ln -s ` (с пробелом в конце, Enter пока не нажимайте) → перетащите папку датасета из Finder в окно Terminal (появится путь) → допишите ` data/raw/hydrowatch_amur` → Enter. Целиком выглядит так:
```bash
ln -s /Users/ВАШЕ_ИМЯ/Downloads/hydrowatch_amur data/raw/hydrowatch_amur
```
Проверка:
```bash
ls data/raw/hydrowatch_amur/
```
→ `pairs.csv  rasters  reference_masks  sample_submission.csv  tables …`. Если `No such file or directory` — ссылка на не ту папку: `rm data/raw/hydrowatch_amur` и повторите.

### 2.3 Положить веса
Найдите `best.pt`: в архиве `hydrowatch_solution_final.tar.gz` (двойной клик распаковывает → внутри `hydrowatch_solution/weights/best.pt`) или на обучающей машине в `outputs/siamese_resnet18_coverage_finetune/best.pt`.
Наберите `cp ` → перетащите `best.pt` в Terminal → допишите ` weights/best.pt` → Enter:
```bash
cp /Users/ВАШЕ_ИМЯ/Downloads/best.pt weights/best.pt
```
```bash
ls -lh weights/best.pt
shasum -a 256 weights/best.pt
```
Первая строка покажет размер (например `93M` или `180M`) — **запишите его** (нужен в 5.2). Вторая должна напечатать ровно
`6c7ef209ffe3f470d862ebeb2b4770d8d480406fd81a47d9bf7254323b8c2d06  weights/best.pt`.
Сумма другая — это не тот чекпоинт, которым считался submission. Найдите правильный; если его нет — напишите мне (сабмит и сервис от весов не зависят, но в README указана именно эта сумма).

### 2.4 Есть ли снимки Sentinel
```bash
ls data/raw/hydrowatch_amur/rasters/flood_2021_08_zeya/svobodny/
```
- Есть `S1_pre.tif`, `S1_peak.tif` (и, возможно, `SENTINEL2_*.tif`) → **снимки есть**, шаг 7 доступен.
- Только `AUX_terrain_gsw.tif`, `*.json`, `*.csv` → **снимков нет** — это нормально: сервис, тесты, Docker и сабмит от них не зависят; шаг 7 пропускаем.

---

## Шаг 3. Окружение, тесты, сервис, скриншоты (15 мин)

### 3.1 Установить окружение
```bash
uv sync --extra service --extra dev
```
Что увидите: `Using CPython 3.12.x` (uv сам скачает Python), `Resolved … packages`, много `Downloading …` (torch ≈ 150 МБ), `Installed … packages`. 3–7 минут. В папке появится скрытая `.venv` (в git не попадёт). `uv.lock` может обновиться — нормально, он закоммитится в шаге 9.
- `error: Failed to download …` — сеть; повторите команду.
- Другая ошибка — скопируйте последние 20 строк и пришлите мне.

### 3.2 Тесты и линтер
```bash
uv run pytest -q
```
30–90 секунд; последняя строка `22 passed in 45.12s` (секунды любые). `warnings` — не страшно. Если есть `failed` — пришлите вывод.
```bash
uv run ruff check .
```
→ `All checks passed!`

### 3.3 Запустить сервис
```bash
uv run hydrowatch-service --data-root data/raw/hydrowatch_amur --port 8000
```
Через 3–10 секунд:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```
Окно Terminal «зависает» — так и должно быть: пока оно открыто, сервер работает. Не закрывайте его.

### 3.4 Проверить в браузере (это сценарий демо на защите)
Safari/Chrome → адрес **http://localhost:8000** → Enter. Слева карта, справа панель.
1. Блок **«1. Территория и даты»** → список **«Готовая пара (район × событие)»** → выберите другую пару (например, Поярково 2021‑06). Карта перелетит, слои перерисуются, в **«3. Сводный отчёт»** обновятся площади (га) и таблица по типам поверхности.
2. Блок **«2. Слои»** → галочка **«Режим сравнения «до | пик» (шторка)»** → на карте появится вертикальная линия; тяните её мышью: слева вода «до», справа вода «пик». Снимите галочку — обычный режим (слои «Вода «до»», «Вода «пик»», «Прирост (затопление)», «Убыль», ползунок «Прозрачность»).
3. Кнопка **«нарисовать bbox»** → на карте протяните прямоугольник (зажать левую кнопку, вести, отпустить) → **«Рассчитать»** → отчёт справа пересчитается только по прямоугольнику (площади уменьшатся). **«весь район»** возвращает весь AOI.
4. Ниже, блок **«Выгрузки»** → **«GeoJSON: Прирост (затопление)»** и **«Отчёт CSV»** — файлы скачаются в Загрузки. Откройте CSV: площади и разбивка по покрову.
5. Ссылка **«REST API (Swagger)»** внизу панели (= http://localhost:8000/docs) → список эндпоинтов `GET /api/pairs`, `POST /api/analyze` и т. д.

### 3.5 Скриншоты
Нужны три картинки (для слайдов и папки `docs/screenshots`):
- `01_map_swipe.png` — карта со включённой шторкой (п. 2);
- `02_report_panel.png` — правая панель с отчётом после расчёта по bbox (п. 3);
- `03_swagger.png` — страница /docs (п. 5).

Снять: **Cmd+Shift+4** → курсор‑прицел → выделить область → отпустить. Файл `Снимок экрана 2026‑09‑22 в 14.03.11.png` появится на Рабочем столе.
Переложить: откройте **новую вкладку** Terminal (Cmd+T), затем
```bash
cd ~/Desktop/hydrowatch-amur
open docs/screenshots
```
— откроется окно Finder с нужной папкой. Перетащите туда три снимка с Рабочего стола и переименуйте (клик по имени → Enter → ввести имя → Enter). Проверка:
```bash
ls docs/screenshots
```
→ `01_map_swipe.png  02_report_panel.png  03_swagger.png  README.md`.

### 3.6 Остановить сервис
Кликните в первую вкладку Terminal (где строки `INFO:`) → **Control+C** → появятся `Shutting down` … `Finished server process` и приглашение `%`.

---

## Шаг 4. Проверить submission (2 мин)

```bash
uv run hydrowatch-check-submission --root data/raw/hydrowatch_amur --submission predictions/submission.csv --mask-dir predictions/masks
```
→ `submission validation: OK` (11 строк, id пар, маски есть и совпадают по сетке).
```bash
uv run python scripts/reference_audit.py --root data/raw/hydrowatch_amur --submission predictions/submission.csv --output outputs/reference_audit
```
10–40 секунд; печатает несколько Markdown‑таблиц; среди последних строк:
`**Score = 0.7584** (Q_flood=0.6994, Q_peak=0.7681, Q_pre=0.6803, Spec_base=1.0000)` — совпадает с отчётом (допустимо расхождение в 4‑м знаке). Копия сохранена в `outputs/reference_audit/` (в git не идёт).

---

## Шаг 5. Веса на Яндекс.Диск и ссылка в README (5 мин)

### 5.1 Загрузить
1. https://disk.yandex.ru → войти.
2. Жёлтая кнопка **«Загрузить»** (слева вверху) → Рабочий стол → hydrowatch-amur → weights → `best.pt` → **Открыть** → дождаться 100 % в окошке справа внизу.
3. Правой кнопкой по `best.pt` → **«Поделиться»** → включить **«Доступ по ссылке»** → **«Скопировать ссылку»**. В буфере ссылка вида `https://disk.yandex.ru/d/AbCdEfGh12345`.

### 5.2 Вставить в README
Замените `https://disk.yandex.ru/d/XXXX` на свою ссылку (кавычки и `#` не трогайте):
```bash
sed -i '' 's#<ССЫЛКА НА ЯНДЕКС.ДИСК>#https://disk.yandex.ru/d/XXXX#' README.md
grep -n "disk.yandex" README.md
```
`grep` покажет строку 106 с вашей ссылкой. Ничего не показал — ссылка вставлена не между `#…#`; повторите.
Размер файла из 2.3 (подставьте свой):
```bash
sed -i '' 's#(≈ размер см. по ссылке)#(≈ 93 МБ)#' README.md
```
Альтернатива без sed: `open -a TextEdit README.md` → Cmd+F → найти `<ССЫЛКА НА ЯНДЕКС.ДИСК>` → заменить → Cmd+S → закрыть.

---

## Шаг 6. Docker (15–20 мин, лимит — 20 минут)

### 6.1 Docker запущен?
Кит в строке меню есть и не анимируется. Нет — Программы → Docker → открыть → дождаться **Engine running**.

### 6.2 Собрать и запустить
Нужен **реальный** путь к датасету (не ярлык `data/raw/...`). Наберите `HYDROWATCH_DATA_DIR=` (без пробелов) → перетащите папку датасета из Finder → допишите ` docker compose up --build` → Enter:
```bash
HYDROWATCH_DATA_DIR=/Users/ВАШЕ_ИМЯ/Downloads/hydrowatch_amur docker compose up --build
```
Пойдут строки `[+] Building …`, `=> [service …] RUN pip install …` — 3–6 минут при первом запуске. Затем:
```
 ✔ Container hydrowatch-service  Created
Attaching to hydrowatch-service
hydrowatch-service  | INFO:     Uvicorn running on http://0.0.0.0:8000
```
Откройте http://localhost:8000 — та же карта. Это доказательство воспроизводимости; при желании снимите скриншот окна Terminal → `docs/screenshots/04_docker.png`.

### 6.3 Остановить
**Control+C**, затем
```bash
docker compose down
```
→ `Container hydrowatch-service  Removed`.

### 6.4 Если не работает
- `Cannot connect to the Docker daemon` → Docker Desktop не запущен (6.1).
- `port is already allocated` / `address already in use` → ещё работает сервис из 3.3 в другой вкладке: остановите его Control+C.
- Красные `ERROR` внутри сборки → скопируйте последние 30 строк и пришлите мне. На сдачу не влияет: дальше идём без Docker, Dockerfile из репозитория **не удаляем** (локальный запуск через `uv` описан в README и допускается постановкой).

---

## Шаг 7. Необязательно (только если в 2.4 снимки есть): абляции на Apple GPU

Идёт в фоне 15–40 минут на конфигурацию, пока вы делаете слайды. Новая вкладка (Cmd+T):
```bash
cd ~/Desktop/hydrowatch-amur
uv run hydrowatch-predict --root data/raw/hydrowatch_amur --checkpoint weights/best.pt \
  --output-dir outputs/ablation_no_optical --thresholds 0.95 0.78 0.95 \
  --patch-size 384 --stride 256 --batch-size 4 --device mps --ablation no_optical \
&& uv run python scripts/reference_audit.py --root data/raw/hydrowatch_amur \
  --submission outputs/ablation_no_optical/submission.csv --output outputs/ablation_no_optical/audit
```
(6 строк копируются целиком.) В конце — таблица по парам и строка `**Score = …** (Q_flood=…, Q_peak=…, Q_pre=…, Spec_base=…)`.

Куда вписать: `open -a TextEdit REPORT.md` → Cmd+F `Без оптики` → в этой строке таблицы вместо `⚠️ ЗАПОЛНИТЬ | | | | | |` напишите шесть чисел через ` | ` в порядке `Q_flood | Q_peak | Q_pre | Spec_base | Score | q_flood Зеи` (последнее — из таблицы по парам: строка `flood_2021_08_zeya__svobodny`, столбец `q_flood`) → Cmd+S.
Если есть время: `--ablation no_aux` (папка `outputs/ablation_no_aux`, строка «Без AUX») и вариант **без** `--ablation`, но с `--thresholds 0.5 0.5 0.5` (папка `outputs/ablation_t05`, строка «Пороги 0.5/0.5/0.5»).

Что не заполнили — удаляется одной командой (убирает ровно строки с пометкой; если ничего не заполняли — 5 строк):
```bash
grep -v "ЗАПОЛНИТЬ" REPORT.md > /tmp/r.md && mv /tmp/r.md REPORT.md
grep -c "ЗАПОЛНИТЬ" REPORT.md
```
→ `0`.
**Не трогайте** `predictions/` и не переписывайте `submission.csv` результатами с Mac: сдаётся расчёт с CUDA, который совпадает с масками и контрольными суммами (MPS может отличаться в 3‑м знаке — для абляций это неважно).

---

## Шаг 8. Презентация (25 мин)

1. https://slides.google.com → **«+» Пустая презентация** (или Keynote) → название `HydroWatch Amur — <команда>`.
2. Ровно 10 слайдов, содержимое — таблица в `PRESENTATION.md` (`open PRESENTATION.md`). Заголовки:
   1 Задача · 2 Что в данных · 3 Почему не «Оцу + разность» · 4 Схема решения · 5 Эксперименты · 6 Критика эталона · 7 Сервис — демо · 8 Продукт и обновление · 9 Воспроизводимость и ресурсы · 10 Ограничения и развитие.
3. Картинки: слайд 4 — скриншот схемы из README §1 (открыть README на GitHub‑ветке или позже на GitVerse → Cmd+Shift+4 по блоку); слайд 7 — `docs/screenshots/01_map_swipe.png` и `02_report_panel.png` (Вставка → Изображение → Загрузить с компьютера); слайд 9 — `03_swagger.png` / скриншот Docker; слайд 5 — таблица из REPORT.md §5 (Score 0.758: Q_flood 0.699 / Q_peak 0.768 / Q_pre 0.680 / Spec 1.00; отложенная Зея q_flood 0.53 / q_pre 1.00 / q_peak 0.57); слайд 6 — цифры из REPORT.md §3 (5 из 11 пар без постоянной воды в эталоне; Константиновка 2021‑06: 30 га «до» против 5436 га постоянной воды; Зея JSON 2179 га против маски 2484 га).
4. Экспорт: **Файл → Скачать → Документ PDF (.pdf)** → файл в Загрузках.
5. Переложить: `open docs` → перетащить PDF из Загрузок в открывшуюся папку → переименовать в `presentation.pdf`. Проверка: `ls docs` → `presentation.pdf  screenshots`.

Отчёт остаётся `REPORT.md` в репозитории. Если форма требует PDF отчёта — после шага 9 открыть REPORT.md на GitVerse → Cmd+P → «Сохранить как PDF» → `docs/report.pdf` → `git add -A && git commit -m "report pdf" && git push`.

---

## Шаг 9. Залить на GitVerse (10 мин)

### 9.1 Аккаунт и пустой репозиторий
1. https://gitverse.ru → **Войти** (справа вверху) → регистрация (Сбер ID или email) → подтвердить почту.
2. Кнопка **«+»** / **«Создать репозиторий»** (справа вверху или «Репозитории → Новый»).
3. Форма: **Название** `hydrowatch-amur`; **Описание** `HydroWatch Amur — мультимодальная сегментация затоплений по Sentinel-1/2`; **Видимость — Публичный**; галочки «Инициализировать README / .gitignore / лицензия» — **не ставить** → **Создать**.
4. Откроется пустой репозиторий; скопируйте адрес `https://gitverse.ru/ЛОГИН/hydrowatch-amur.git` (кнопка «Клонировать» → HTTPS). Ваш **логин** — часть адреса.

### 9.2 Токен доступа
Аватар (справа вверху) → **Настройки** → слева **«Токены доступа»** → **«Создать токен»** → имя `macbook`, срок — максимальный, права — **чтение и запись репозиториев** (или «все») → **Создать** → строку токена **скопируйте сразу** в Заметки: второй раз её не покажут.

### 9.3 Подготовка (папка `~/Desktop/hydrowatch-amur`)
Представиться git (один раз на компьютере):
```bash
git config --global user.name "Имя Фамилия"
git config --global user.email "ваша@почта.ru"
```
Убрать незаполненные строки отчёта (безопасно повторять):
```bash
grep -v "ЗАПОЛНИТЬ" REPORT.md > /tmp/r.md && mv /tmp/r.md REPORT.md
```
Пересчитать контрольные суммы (одна длинная команда, копировать целиком):
```bash
find . -type f ! -name SHA256SUMS.txt ! -path './.git/*' ! -path './data/*' ! -path './outputs/*' \
  ! -path './.venv/*' ! -path './weights/*' ! -name '.DS_Store' ! -path '*/__pycache__/*' \
  ! -path './.pytest_cache/*' ! -path './.ruff_cache/*' -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS.txt
wc -l SHA256SUMS.txt
```
→ около 95–105 строк.

### 9.4 Локальный репозиторий и проверка состава
```bash
git init -b main
```
→ `Initialized empty Git repository in /Users/…/hydrowatch-amur/.git/`
```bash
git add -A
git status --short | grep -E " (data|weights|outputs|\.venv)/"
```
Вторая команда должна **ничего не вывести** (датасет, веса и результаты не попали). Если вывела строку — пришлите её мне.
```bash
git status --short | wc -l
git status --short | grep predictions/masks | wc -l
```
→ примерно `95–105` и ровно `22`.

### 9.5 Коммит и отправка
```bash
git commit -m "HydroWatch Amur: multimodal flood segmentation, inference, service, report"
```
→ `[main (root-commit) a1b2c3d] … 98 files changed, … insertions(+)`.
```bash
git remote add origin https://gitverse.ru/ЛОГИН/hydrowatch-amur.git
git push -u origin main
```
Terminal спросит:
- `Username for 'https://gitverse.ru':` → **логин GitVerse** → Enter;
- `Password for 'https://ЛОГИН@gitverse.ru':` → **токен** (Cmd+V, символы не видны) → Enter.
Успех: `Writing objects: 100% …`, `* [new branch] main -> main`, `branch 'main' set up to track 'origin/main'`. macOS сохранит токен в Связке ключей — больше не спросит.

### 9.6 Проверить в браузере
Обновите страницу репозитория: README со схемой и разделами 1–11; открываются `REPORT.md`, `PRESENTATION.md`; в `predictions/masks` 22 `.tif`; в `docs` — `presentation.pdf` и `screenshots`. Ссылка на веса из README открывается в режиме инкогнито (Cmd+Shift+N).

### 9.7 Если push не прошёл
- `Authentication failed` → токен неверный/без прав на запись: создайте новый (9.2) и повторите `git push -u origin main`. Если пароль больше не спрашивают, а ошибка та же — **Связка ключей** → поиск `gitverse` → удалить запись → повторить.
- `file size limit` / `exceeds` → попал большой файл. Найти: `git ls-files -z | xargs -0 du -k | sort -n | tail -5`; добавить в игнор: `echo 'путь/файл' >> .gitignore`; убрать из индекса и отправить: `git rm --cached путь/файл && git add -A && git commit -m "drop big file" && git push -u origin main`.
- `Repository not found` → опечатка в адресе/логине: `git remote set-url origin https://gitverse.ru/ЛОГИН/hydrowatch-amur.git` → снова push.

---

## Шаг 10. Поле «сервер» (необязательно, 20 мин)

Скорее всего, это адрес работающего прототипа.

**А. VPS на неделю (300–600 ₽).** На примере Timeweb Cloud (Selectel / Yandex Cloud аналогично):
1. timeweb.cloud → регистрация → **Облачные серверы → Создать** → ОС **Ubuntu 22.04** → 2 vCPU / 4 ГБ RAM / 30 ГБ → **Заказать**. Через 1–2 минуты в карточке сервера появятся **IP‑адрес** и **пароль root**.
2. В Terminal на Mac:
```bash
ssh root@IP_АДРЕС
```
(`Are you sure you want to continue connecting?` → `yes` → пароль root.) Дальше команды выполняются на сервере:
```bash
apt update && apt install -y docker.io docker-compose-v2 git
git clone https://gitverse.ru/ЛОГИН/hydrowatch-amur.git && cd hydrowatch-amur
mkdir -p data/raw/hydrowatch_amur
docker compose up -d --build
```
3–6 минут. В браузере `http://IP_АДРЕС:8000` — карта. Не открывается → в панели хостинга «Файрвол/Сеть» разрешить входящий TCP‑порт 8000.
3. Чтобы работала разбивка по типам поверхности, докиньте AUX‑растры (67 МБ). На Mac, новая вкладка:
```bash
scp -r /ПУТЬ/К/hydrowatch_amur/rasters /ПУТЬ/К/hydrowatch_amur/pairs.csv root@IP_АДРЕС:/root/hydrowatch-amur/data/raw/hydrowatch_amur/
```
затем на сервере `docker compose restart`.
4. В поле «сервер» — `http://IP_АДРЕС:8000` (Swagger: `http://IP_АДРЕС:8000/docs`). Сервер не удаляйте до конца оценки.

**Б. Без сервера.** В поле: «Сервис (карта + REST API) поставляется в репозитории: `docker compose up --build` (README, раздел 7) либо `uv run hydrowatch-service`; демонстрация на защите». И уточните в чате организаторов, что именно ожидается в этом поле.

---

## Шаг 11. Архив масок и сабмит на сайте (10 мин)

### 11.1 Архив масок в требуемой структуре `predictions/<pair_id>_flood.tif`
```bash
cd ~/Desktop/hydrowatch-amur
rm -rf /tmp/pack && mkdir -p /tmp/pack/predictions
cp predictions/masks/*_flood.tif /tmp/pack/predictions/
cp predictions/submission.csv /tmp/pack/predictions/
ls /tmp/pack/predictions | wc -l
```
→ `12` (11 масок + submission.csv).
```bash
(cd /tmp/pack && zip -r ~/Desktop/hydrowatch_predictions_masks.zip predictions)
ls -lh ~/Desktop/hydrowatch_predictions_masks.zip
```
→ архив ~1–2 МБ на Рабочем столе.

### 11.2 Выложить архив и вписать ссылку
Яндекс.Диск → **«Загрузить»** → `hydrowatch_predictions_masks.zip` → **«Поделиться»** → **«Скопировать ссылку»**. Затем (подставьте ссылку):
```bash
sed -i '' 's#<ССЫЛКА НА АРХИВ МАСОК>#https://disk.yandex.ru/d/YYYY#' README.md
grep -c "ССЫЛКА НА" README.md
```
→ `0` (плейсхолдеров не осталось).
```bash
git add -A && git commit -m "Add links to weights and mask archive" && git push
```

### 11.3 Форма на сайте организаторов
| Поле | Что вставить |
|---|---|
| Сабмит | файл `~/Desktop/hydrowatch-amur/predictions/submission.csv` (в диалоге: Рабочий стол → hydrowatch-amur → predictions) |
| Презентация | `~/Desktop/hydrowatch-amur/docs/presentation.pdf` |
| Репозиторий | `https://gitverse.ru/ЛОГИН/hydrowatch-amur` |
| Сервер | `http://IP_АДРЕС:8000` (10А) или текст из 10Б |
| Маски (отдельное поле / чат / почта организаторов) | ссылка на `hydrowatch_predictions_masks.zip` |

Перед «Отправить» откройте `submission.csv` двойным кликом (Numbers/Excel): заголовок `pair_id, flood_ha, water_pre_ha, water_peak_ha`, 11 строк данных, без пустых ячеек.

---

## Финальный чек‑лист (2 мин)

- [ ] На GitVerse открывается README; в нём рабочие ссылки на `best.pt` и архив масок, SHA‑256 весов.
- [ ] В репозитории нет `data/`, `outputs/`, `.venv/`, `weights/`; есть `predictions/masks/*_flood.tif` (11) и `*_all.tif` (11).
- [ ] `submission.csv` загружен на сайт; архив масок доступен по ссылке.
- [ ] В REPORT.md нет слова «ЗАПОЛНИТЬ» (`grep -c ЗАПОЛНИТЬ REPORT.md` → 0); README без `<ССЫЛКА НА …>`.
- [ ] PDF презентации загружен; в нём скриншоты сервиса и таблица метрики.
- [ ] Ссылки Яндекс.Диска открываются в режиме инкогнито.
- [ ] `docker compose up` показывал карту хотя бы один раз (или честно указан локальный запуск через `uv`).
- [ ] Папку `only_data_arena` можно удалить; `~/Desktop/hydrowatch-amur` сохранить до конца хакатона.
