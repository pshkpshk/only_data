"""Собрать docs/presentation.pptx (10 слайдов, стиль: белый + яркий синий, лаконично).

Запуск:  python scripts/build_presentation.py --team "Название команды" [--score 0.768]
Скриншоты из --screenshots (по умолчанию docs/screenshots): 01_map_swipe.png, 02_report_panel.png,
03_swagger.png — вставляются автоматически; если файла нет, остаётся рамка-подсказка.
Зависимость: python-pptx (uv run --with python-pptx python scripts/build_presentation.py ...).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

BLUE = RGBColor(0x0A, 0x7A, 0xFF)  # яркий насыщенный синий
BLUE_DARK = RGBColor(0x0B, 0x3D, 0x91)
TINT = RGBColor(0xEA, 0xF3, 0xFF)  # светло-голубая подложка карточек
INK = RGBColor(0x11, 0x18, 0x27)  # основной текст
GREY = RGBColor(0x6B, 0x72, 0x80)
LINE = RGBColor(0xD6, 0xE6, 0xFA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "Helvetica Neue"

W, H = Inches(13.333), Inches(7.5)
M = Inches(0.7)  # поле


# ----------------------------------------------------------------------------- primitives
def rect(slide, left, top, width, height, fill=None, line=None, rounded=False, radius=0.12):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE, left, top, width, height
    )
    if rounded:
        shape.adjustments[0] = radius
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    shape.shadow.inherit = False
    shape.text_frame.text = ""
    return shape


def text(slide, left, top, width, height, content, size=16, color=INK, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=4, font=FONT):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    items = content if isinstance(content, list) else [content]
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(spacing)
        run = p.add_run()
        run.text = item
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = font
    return box


def bullets(slide, left, top, width, height, items, size=16, color=INK, marker="—"):
    return text(slide, left, top, width, height, [f"{marker}  {item}" for item in items],
                size=size, color=color, spacing=8)


def card(slide, left, top, width, height, title, body=None, big=None, fill=TINT, title_color=BLUE):
    """Карточка: заголовок синим, крупное число (big) и/или короткий текст."""
    rect(slide, left, top, width, height, fill=fill, rounded=True, radius=0.08)
    pad = Inches(0.22)
    inner = width - 2 * pad
    y = top + pad
    if big is not None:
        big_size = next((sz for sz in (40, 32, 26) if Emu(inner).pt > len(big) * sz * 0.56), 22)
        text(slide, left + pad, y, inner, Inches(0.85), big, size=big_size, bold=True, color=BLUE)
        y += Inches(0.8)
    title_size = 15
    title_lines = max(1, -(-int(len(title) * title_size * 0.53) // max(1, int(Emu(inner).pt))))
    title_h = Inches(0.3) * title_lines + Inches(0.08)
    text(slide, left + pad, y, inner, title_h, title, size=title_size, bold=True, color=title_color)
    if body:
        text(slide, left + pad, y + title_h, inner, height - (y - top) - title_h - pad, body,
             size=13, color=GREY, spacing=3)


def chip(slide, left, top, width, label, fill=BLUE, color=WHITE, size=13, height=None):
    height = height or Inches(0.42)
    shape = rect(slide, left, top, width, height, fill=fill, rounded=True, radius=0.5)
    tf = shape.text_frame
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = label
    run.font.size = Pt(size)
    run.font.bold = True
    run.font.color.rgb = color
    run.font.name = FONT
    return shape


def arrow(slide, left, top, width=None, height=None):
    width, height = width or Inches(0.45), height or Inches(0.3)
    shape = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = BLUE
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def picture(slide, path: Path, left, top, width, height, hint):
    if path.is_file():
        pic = slide.shapes.add_picture(str(path), left, top)
        ratio = min(width / pic.width, height / pic.height)
        pic.width, pic.height = Emu(int(pic.width * ratio)), Emu(int(pic.height * ratio))
        pic.left = left + int((width - pic.width) / 2)
        pic.top = top
        frame = rect(slide, pic.left - Emu(9525), pic.top - Emu(9525), pic.width + Emu(19050),
                     pic.height + Emu(19050), line=LINE, rounded=False)
        frame.fill.background()
        return pic
    shape = rect(slide, left, top, width, height, fill=TINT, line=LINE, rounded=True, radius=0.04)
    tf = shape.text_frame
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = hint
    run.font.size = Pt(13)
    run.font.color.rgb = GREY
    run.font.name = FONT
    return shape


def frame(slide, number, title, lead=None):
    """Белый фон, синий номер, заголовок, тонкая линия, подпись внизу."""
    rect(slide, 0, 0, W, H, fill=WHITE)
    chip(slide, M, Inches(0.45), Inches(0.6), f"{number:02d}", size=13, height=Inches(0.36))
    text(slide, M + Inches(0.75), Inches(0.36), W - 2 * M - Inches(0.75), Inches(0.6), title,
         size=28, bold=True, color=INK)
    if lead:
        text(slide, M, Inches(1.02), W - 2 * M, Inches(0.5), lead, size=15, color=GREY)
    rect(slide, M, Inches(1.5), W - 2 * M, Emu(12700), fill=LINE)
    text(slide, M, H - Inches(0.5), Inches(8), Inches(0.3),
         "HydroWatch Amur · КосмоХакатон 2026 · кейс «Гидрологический мониторинг»", size=10,
         color=GREY)
    text(slide, W - M - Inches(1.0), H - Inches(0.5), Inches(1.0), Inches(0.3), f"{number} / 10",
         size=10, color=BLUE, bold=True, align=PP_ALIGN.RIGHT)


# ----------------------------------------------------------------------------- deck
def build(out: Path, shots: Path, team: str, score: str) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]
    top = Inches(1.8)
    cw = W - 2 * M  # рабочая ширина

    # 1 ---------------------------------------------------------------- титул
    s = prs.slides.add_slide(blank)
    rect(s, 0, 0, W, H, fill=WHITE)
    rect(s, 0, 0, Inches(0.35), H, fill=BLUE)
    text(s, Inches(1.1), Inches(1.5), Inches(11), Inches(1.3), "HydroWatch Amur", size=60,
         bold=True, color=BLUE)
    text(s, Inches(1.1), Inches(2.75), Inches(11), Inches(1.2),
         ["Динамика затопления по Sentinel-1 + Sentinel-2:",
          "вода «до» и «пик», зона нового затопления, площади и сервис"], size=24, color=INK)
    x = Inches(1.1)
    for label, wdt in (("11 пар · 5 районов Приамурья", Inches(3.4)),
                       (f"Score {score} на платформе", Inches(3.0)),
                       ("docker compose up → карта + API", Inches(3.6))):
        chip(s, x, Inches(4.55), wdt, label, size=14, height=Inches(0.48))
        x += wdt + Inches(0.2)
    text(s, Inches(1.1), Inches(6.3), Inches(11), Inches(0.5),
         f"КосмоХакатон 2026 · кейс «Гидрологический мониторинг» · команда {team}", size=14,
         color=GREY)

    # 2 ---------------------------------------------------------------- задача и метрика
    s = prs.slides.add_slide(blank)
    frame(s, 2, "Задача и метрика", "Пара наблюдений «до / пик» → три карты и три площади")
    third = (cw - 2 * Inches(0.3)) / 3
    card(s, M, top, third, Inches(2.2), "Вход",
         ["Sentinel-1 GRD: VV, VH на две даты", "Sentinel-2 (если нет облаков): B03, B04, B08, B11",
          "Рельеф HAND/уклон, JRC GSW, ESA WorldCover"])
    card(s, M + third + Inches(0.3), top, third, Inches(2.2), "Выход",
         ["Маска воды на дату «до»", "Маска воды на дату «пик»",
          "Маска нового затопления → площади, га"])
    card(s, M + 2 * (third + Inches(0.3)), top, third, Inches(2.2), "Проверка",
         ["11 пар: 8 паводковых + 3 межени", "Маски uint8 на сетке эталона",
          "Площадь по маске = площадь в csv"])
    rect(s, M, top + Inches(2.55), cw, Inches(1.5), fill=BLUE, rounded=True, radius=0.08)
    text(s, M + Inches(0.3), top + Inches(2.7), cw - Inches(0.6), Inches(0.6),
         "Score = 0.45 · Q_flood + 0.25 · Q_peak + 0.15 · Q_pre + 0.15 · Spec_межень",
         size=20, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    text(s, M + Inches(0.3), top + Inches(3.3), cw - Inches(0.6), Inches(0.6),
         "q = 1 − |ошибка площади| / max(эталон, порог); порог 50 га для затопления, 200 га для зеркала",
         size=14, color=WHITE, align=PP_ALIGN.CENTER)
    text(s, M, top + Inches(4.3), cw, Inches(0.6),
         "Главное — площадь нового затопления (45 %); межень штрафует ложные срабатывания",
         size=15, color=GREY)

    # 3 ---------------------------------------------------------------- данные
    s = prs.slides.add_slide(blank)
    frame(s, 3, "Что в данных", "EDA по 11 парам — REPORT.md §2")
    quarter = (cw - 3 * Inches(0.25)) / 4
    for i, (big, title, body) in enumerate((
        ("0.02–2 %", "площади района — новое затопление", "сильный дисбаланс классов"),
        ("5 из 11", "пар без пригодной оптики", "решение обязано работать на одном SAR"),
        ("6 недель", "между «до» и «пик»", "разность масок ловит сезонность, а не паводок"),
        ("0.5 / 3.7 м", "медиана HAND: вода / фон", "рельеф — признак, но не жёсткий фильтр"),
    )):
        card(s, M + i * (quarter + Inches(0.25)), top, quarter, Inches(2.6), title, body, big=big)
    bullets(s, M, top + Inches(2.95), cw, Inches(1.8), [
        "σ⁰: VV бимодальна (вода ≈ −20…−24 дБ); VH ниже на 6–8 дБ и с меньшим контрастом",
        "Разрыв SAR–оптика до 5 суток; сцены двух орбит (32 и 105) с разной геометрией",
        "Эталон: 3 источника (GSW / консенсус / GFM) — не однородный, см. слайд 07",
    ], size=15)

    # 4 ---------------------------------------------------------------- почему не порог
    s = prs.slides.add_slide(blank)
    frame(s, 4, "Почему не «порог Оцу + разность масок»", "Ошибки SAR и оптики независимы — объединять надо признаки, а не маски")
    for i, (title, body) in enumerate((
        ("SAR, порог по VV", ["ветровая рябь и радиотени", "гладкие сухие поверхности — «вода»",
                              "пропуски на волнующейся воде"]),
        ("Оптика, NDWI", ["мутная паводковая вода", "тени облаков", "снимка часто просто нет"]),
        ("Разность за 6 недель", ["сезонный ход уровня", "разные орбиты и углы",
                                  "«затопление» = любое изменение"]),
    )):
        card(s, M + i * (third + Inches(0.3)), top, third, Inches(2.5), title, body)
    rect(s, M, top + Inches(2.85), cw, Inches(1.35), fill=BLUE, rounded=True, radius=0.1)
    text(s, M + Inches(0.3), top + Inches(3.0), cw - Inches(0.6), Inches(1.1),
         ["Наш ответ: одна сеть видит оба сенсора и рельеф одновременно,",
          "а новое затопление предсказывает отдельной головой с гидрологической логикой"],
         size=18, bold=True, color=WHITE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

    # 5 ---------------------------------------------------------------- схема решения
    s = prs.slides.add_slide(blank)
    frame(s, 5, "Схема решения", "Siamese multimodal U-Net: два энкодера · temporal-признаки · три головы")
    col_w, gap, in_h, pitch = Inches(2.5), Inches(0.42), Inches(1.05), Inches(1.12)
    y0 = top + Inches(0.05)
    for i, (title, body) in enumerate((
        ("S1 «до» / «пик»", "VV, VH, |Δ|"), ("S2 «до» / «пик»", "B03 B04 B08 B11"),
        ("AUX", "HAND · GSW · уклон"),
    )):
        card(s, M, y0 + i * pitch, col_w, in_h, title, body, fill=TINT)
    ex = M + col_w + gap
    for i, (title, body) in enumerate((
        ("ResNet-18 · SAR", "обучен с нуля"), ("ResNet-18 · оптика", "SSL4EO-S12 MoCo"),
    )):
        rect(s, ex, y0 + i * pitch, col_w, in_h, fill=BLUE, rounded=True, radius=0.1)
        text(s, ex + Inches(0.15), y0 + i * pitch + Inches(0.15), col_w - Inches(0.3), Inches(0.4),
             title, size=15, bold=True, color=WHITE)
        text(s, ex + Inches(0.15), y0 + i * pitch + Inches(0.55), col_w - Inches(0.3), Inches(0.4),
             body, size=12, color=WHITE)
        arrow(s, M + col_w + Inches(0.0), y0 + i * pitch + Inches(0.38), width=gap, height=Inches(0.28))
    tx = ex + col_w + gap
    tall = 2 * pitch + in_h - pitch  # высота двух рядов
    card(s, tx, y0, col_w, tall, "Temporal-признаки",
         ["pre · peak · Δ на каждом масштабе", "+ AUX (HAND, GSW, WorldCover)", "+ флаг наличия оптики"])
    arrow(s, ex + col_w, y0 + tall / 2 - Inches(0.14), width=gap, height=Inches(0.28))
    ux = tx + col_w + gap
    rect(s, ux, y0, col_w, tall, fill=BLUE_DARK, rounded=True, radius=0.1)
    text(s, ux + Inches(0.15), y0 + Inches(0.2), col_w - Inches(0.3), Inches(0.5), "U-Net decoder",
         size=17, bold=True, color=WHITE)
    text(s, ux + Inches(0.15), y0 + Inches(0.75), col_w - Inches(0.3), Inches(1.3),
         ["skip-connections", "окна 384×384, stride 256", "modality dropout 50 %"], size=12,
         color=WHITE)
    arrow(s, tx + col_w, y0 + tall / 2 - Inches(0.14), width=gap, height=Inches(0.28))
    oy = y0 + 2 * pitch + in_h + Inches(0.25)
    for i, (label, thr) in enumerate((("вода «до»", "порог 0.95"), ("вода «пик»", "порог 0.78"),
                                      ("новое затопление", "порог 0.95"))):
        bw = (cw - 2 * Inches(0.2)) / 3
        bx = M + i * (bw + Inches(0.2))
        rect(s, bx, oy, bw, Inches(0.78), fill=TINT, rounded=True, radius=0.2)
        text(s, bx, oy + Inches(0.06), bw, Inches(0.4), label, size=16, bold=True, color=BLUE,
             align=PP_ALIGN.CENTER)
        text(s, bx, oy + Inches(0.42), bw, Inches(0.3), thr, size=12, color=GREY,
             align=PP_ALIGN.CENTER)
    text(s, M, oy + Inches(0.9), cw, Inches(0.5),
         "Loss: focal Tversky + Dice/BCE + штраф за площадь + логика (затопление ⊂ пик, ∩ «до» = ∅)  ·  "
         "пороги калиброваны на отложенной Зее-2021  ·  веса best.pt, SHA-256 6c7ef209…2d06",
         size=12, color=GREY)

    # 6 ---------------------------------------------------------------- результаты
    s = prs.slides.add_slide(blank)
    frame(s, 6, "Результаты", "Валидация по событию: Зея-2021 отложена целиком · seed 2026")
    card(s, M, top, Inches(3.6), Inches(2.3), "Score на платформе", "официальный сабмит, 11 пар",
         big=score)
    card(s, M + Inches(3.85), top, Inches(3.6), Inches(2.3), "Self-check по эталону",
         "scripts/reference_audit.py", big="0.758")
    card(s, M + Inches(7.7), top, cw - Inches(7.7), Inches(2.3), "Отложенная Зея-2021 (честная цифра)",
         ["q_flood 0.53 · q_peak 0.57 · q_pre 1.00", "3211 га прогноза против 2179 га в JSON эталона",
          "(2484 га по самой маске эталона)"])
    ty = top + Inches(2.6)
    rows = [("Компонент", "Вес", "Значение"), ("Q_flood", "0.45", "0.699"), ("Q_water_peak", "0.25", "0.768"),
            ("Q_water_pre", "0.15", "0.680"), ("Spec_base (межень)", "0.15", "1.000")]
    for r, (a, b, c_) in enumerate(rows):
        yy = ty + r * Inches(0.36)
        col = BLUE if r == 0 else INK
        text(s, M, yy, Inches(3.2), Inches(0.35), a, size=13, bold=r == 0, color=col)
        text(s, M + Inches(3.2), yy, Inches(0.8), Inches(0.35), b, size=13, bold=r == 0, color=col)
        text(s, M + Inches(4.0), yy, Inches(1.2), Inches(0.35), c_, size=13, bold=True,
             color=BLUE if r else col)
        if r == 0:
            rect(s, M, yy + Inches(0.34), Inches(5.3), Emu(12700), fill=LINE)
    bullets(s, M + Inches(6.2), ty, cw - Inches(6.2), Inches(2.2), [
        "Fine-tune лучшей ранней модели: val loss 0.489 → 0.449 (с нуля — 0.614)",
        "Межень: flood = 0 по типу события, Spec = 1.0 без подгонки под эталон",
        "Абляции тем же чекпоинтом: --ablation no_optical / no_aux (REPORT §6.2)",
    ], size=14)

    # 7 ---------------------------------------------------------------- критика эталона
    s = prs.slides.add_slide(blank)
    frame(s, 7, "Критика эталона", "Воспроизводимый аудит: scripts/reference_audit.py")
    for i, (big, title, body) in enumerate((
        ("5 из 11", "масок воды почти без постоянной воды",
         "сцены орбиты 105 покрывали район частично: Константиновка-2021 — 30 га «до» при 5436 га реки"),
        ("+14 %", "Зея-2021: JSON 2179 га, маска 2484 га", "какую цифру считать эталоном?"),
        ("1156 га", "flood ≠ peak ∧ ¬pre ∧ ¬perm", "фильтры уклон/HAND/MMU применены только к flood"),
    )):
        card(s, M + i * (third + Inches(0.3)), top, third, Inches(2.7), title, body, big=big)
    bullets(s, M, top + Inches(3.0), cw, Inches(1.6), [
        "Наш случай: Поярково — сцены орбиты 105 покрывали 0.3–0.4 % района → scene_overrides.yaml, орбита 32 (100 %)",
        "Вывод: часть «ошибки» модели — ошибка эталона; метрика по площадям видит «сколько», но не «где»",
    ], size=15)

    # 8 ---------------------------------------------------------------- сервис
    s = prs.slides.add_slide(blank)
    frame(s, 8, "Сервис: карта, отчёт, API", "docker compose up --build → http://localhost:8000 · Swagger /docs")
    picture(s, shots / "01_map_swipe.png", M, top, Inches(7.2), Inches(3.9),
            "скриншот 01_map_swipe.png")
    picture(s, shots / "02_report_panel.png", M + Inches(7.45), top, cw - Inches(7.45), Inches(3.9),
            "скриншот 02_report_panel.png")
    x = M
    for label, wdt in (("полигон / bbox + даты", Inches(2.5)), ("слои «до · пик · прирост · убыль», шторка", Inches(4.1)),
                       ("отчёт JSON / CSV, GeoJSON, GeoTIFF", Inches(3.6)), ("REST API", Inches(1.5))):
        chip(s, x, top + Inches(4.1), wdt, label, size=12, height=Inches(0.4))
        x += wdt + Inches(0.12)
    text(s, M, top + Inches(4.6), cw, Inches(0.5),
         "Зея-2021: прирост 3211 га, убыль 640 га, зеркало +71 % · разбивка по типам поверхности и профиль HAND · "
         "ответ по району ≈ 1 с", size=13, color=GREY)

    # 9 ---------------------------------------------------------------- продукт + воспроизводимость
    s = prs.slides.add_slide(blank)
    frame(s, 9, "Обновление по новым сценам и воспроизводимость", "Пользователь — дежурная смена ЦУКС / ЦГМС: решение в часы после пролёта")
    steps = (("1", "Пролёт S1", "выгрузка на сетку района, ≈ 10 мин"),
             ("2", "Маски", "hydrowatch-predict, 10–20 с на GPU"),
             ("3", "POST /api/observations", "сравнение с опорным «до», < 1 с"),
             ("4", "Отчёт и карта", "пересчитывается только разность"))
    sw = (Inches(7.2) - 3 * Inches(0.15)) / 4
    for i, (n, title, body) in enumerate(steps):
        x = M + i * (sw + Inches(0.15))
        rect(s, x, top, sw, Inches(1.9), fill=TINT, rounded=True, radius=0.1)
        chip(s, x + Inches(0.15), top + Inches(0.15), Inches(0.45), n, size=12, height=Inches(0.36))
        text(s, x + Inches(0.15), top + Inches(0.6), sw - Inches(0.3), Inches(0.5), title, size=13,
             bold=True, color=BLUE)
        text(s, x + Inches(0.15), top + Inches(1.0), sw - Inches(0.3), Inches(0.9), body, size=12,
             color=GREY)
    bullets(s, M, top + Inches(2.15), Inches(7.2), Inches(2.6), [
        "Хранится: опорное состояние «до», постоянная вода GSW, AUX; разные орбиты — отдельные ряды",
        "docker compose up · uv.lock + pyproject · seed 2026 · SHA-256 весов и артефактов · 22 теста",
        "Инференс ≈ 2–3 ГБ VRAM, 10–20 с на пару; сервис < 1 ГБ RAM; доступ к весам ≥ 10 рабочих дней",
    ], size=14)
    picture(s, shots / "03_swagger.png", M + Inches(7.45), top, cw - Inches(7.45), Inches(4.7),
            "скриншот 03_swagger.png")

    # 10 --------------------------------------------------------------- ограничения и развитие
    s = prs.slides.add_slide(blank)
    frame(s, 10, "Ограничения и развитие")
    half = (cw - Inches(0.3)) / 2
    rect(s, M, top, half, Inches(4.4), fill=TINT, rounded=True, radius=0.06)
    text(s, M + Inches(0.3), top + Inches(0.2), half - Inches(0.6), Inches(0.5), "Что не решено",
         size=18, bold=True, color=GREY)
    bullets(s, M + Inches(0.3), top + Inches(0.75), half - Inches(0.6), Inches(3.5), [
        "Локальный угол падения не используется",
        "Затопленная растительность (double-bounce) не картируется как вода",
        "Переоценка на широких поймах: Поярково 3367 га против 2167 га",
        "Пороги калиброваны на одном отложенном событии",
    ], size=15, color=INK)
    rect(s, M + half + Inches(0.3), top, half, Inches(4.4), fill=BLUE, rounded=True, radius=0.06)
    text(s, M + half + Inches(0.6), top + Inches(0.2), half - Inches(0.6), Inches(0.5), "Что дальше",
         size=18, bold=True, color=WHITE)
    bullets(s, M + half + Inches(0.6), top + Inches(0.75), half - Inches(0.6), Inches(3.5), [
        "Временной ряд S1 вместо пары: опорная вода и сезонная норма",
        "Расширение выборки событиями 2013 и 2022 гг.",
        "Слой double-bounce для затопленной растительности",
        "Агрегация по бассейнам, связка с уровнями Росгидромета",
        "Инкрементальный контур обновления — в продукт",
    ], size=15, color=WHITE)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    print(f"saved {out} ({out.stat().st_size / 1e6:.1f} MB), slides: {len(prs.slides)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/presentation.pptx"))
    parser.add_argument("--screenshots", type=Path, default=Path("docs/screenshots"))
    parser.add_argument("--team", default="<название команды>")
    parser.add_argument("--score", default="0.768", help="Score на платформе для титула и слайда 6")
    args = parser.parse_args()
    build(args.out, args.screenshots, args.team, args.score)


if __name__ == "__main__":
    main()
