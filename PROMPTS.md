# PROMPTS.md — Бібліотека промптів для Home Improvement Photo Processor
# Джерела: Google Cloud Official Nano Banana Guide (March 2026), Leonardo.ai,
# LaoZhang AI Blog, Atlabs AI, bananaprompts.com, chasejarvis.com

---

## ГОЛОВНІ ПРАВИЛА NANO BANANA (офіційний Google, 2026)

### Структура промпту для редагування:
```
[Дієслів-дія] + [Що змінити + де] + [Що залишити незмінним] + [Технічні параметри] + [Мета]
```

### Золоті правила:
1. Починай з сильного дієслова: "Retouch", "Inpaint", "Match", "Correct", "Overlay"
2. Описуй ЩО хочеш — не чого НЕ хочеш ("clean background" а не "no text")
3. Завжди додавай: "Leave everything else exactly unchanged"
4. Для тексту в лапках: "Company Name" — модель сприймає як literal string
5. Вказуй позицію точно: "bottom-left corner", "top-right at 10% from edge"
6. Для збігу стилів між фото: обидва в одному запиті + "identical visual style"
7. НІКОЛИ не пиши "remove watermark" або "delete logo" → safety фільтр

---

## ЗАДАЧА 1: ОЧИЩЕННЯ ТЕКСТУ (без зміни фото)

### Принцип (з офіційного Google гайду):
Semantic masking через текст — описуй область, яку треба змінити,
і явно вказуй що залишається без змін.

### ПРОМПТ 1-A — Мінімальний (Flash модель, найнадійніший):
```
Inpaint the overlaid text captions and graphic badges on this photo.
Fill each area with the natural background texture and color from
the surrounding region. Leave the entire photo content — equipment,
walls, surfaces, and lighting — exactly unchanged.
```

### ПРОМПТ 1-B — Детальний з локацією:
```
This home improvement photo has overlaid text captions and graphic
elements. Perform semantic inpainting on each overlay:
- Detect all text overlays, caption boxes, and graphic badges
- For each one: fill the area with background texture that matches
  the surrounding pixels in color, grain, and lighting direction
- Apply no changes to any other part of the photo
- Preserve original exposure, white balance, and composition exactly
The result must look as if the overlays were never there.
```

### ПРОМПТ 1-C — Якщо текст у відомому місці:
```
Inpaint the text overlay located in the [bottom-right / top-left /
center-bottom] corner of this photo. Fill it with background texture
matching the surrounding area. Do not modify any other part of the image.
```

### ПРОМПТ 1-D — Якщо модель відмовляє (fallback, максимально нейтральний):
```
Please retouch this construction site photo. There are some graphic
elements overlaid on the image — clean them up so the background
shows through naturally. Improve the overall photo quality slightly.
Return the retouched photo.
```

---

## ЗАДАЧА 2: ЄДИНИЙ СТИЛЬ (світло + якість для серії фото)

### Принцип (з chasejarvis.com + Google Cloud, 2026):
Nano Banana Pro може брати стиль з одного референсного фото
і застосовувати до інших. Для пар — обидва фото в одному запиті.

### ПРОМПТ 2-A — Пара До/Після (2 фото в 1 запиті):
```
I am providing two home improvement photos as a BEFORE/AFTER pair.
FIRST IMAGE = BEFORE the work. SECOND IMAGE = AFTER the work.

Process both images and return them with IDENTICAL visual style:

STEP 1 — Clean up: Inpaint any overlaid text, captions, or graphic
badges in both images so background fills naturally.

STEP 2 — Match style: Apply identical color grading to both:
- White balance: neutral daylight, 5500K color temperature
- Exposure: balanced, no blown highlights, lifted shadows
- Contrast: medium-low — detail visible in dark and bright areas
- Saturation: natural +10%, not oversaturated
- Color cast: remove all yellow/orange/green artificial light cast

STEP 3 — Sharpen details equally in both images:
equipment edges, pipe textures, wall surfaces, structural elements.

STEP 4 — Preserve all installed work exactly as photographed.

Return BOTH processed images. They must look shot in the same
conditions by the same photographer.
```

### ПРОМПТ 2-B — Серія фото під один еталон (референсне фото першим):
```
REFERENCE IMAGE (first): This is the approved master style for this
project. Analyze its: white balance, exposure level, color temperature,
contrast ratio, and overall color grading.

PHOTO TO MATCH (second image): Retouch this photo to match the
visual style of the reference exactly:
- Apply same color temperature and white balance
- Match brightness and shadow depth
- Match contrast and saturation level
- Inpaint any overlaid text or graphic badges naturally
- Sharpen structural details and equipment edges
- Do not alter the actual work, installation, or composition

Output must look like both photos were taken in the same session.
```

### ПРОМПТ 2-C — Стандартний для одного фото (без референсу):
```
Retouch this home improvement photo to professional contractor
portfolio standard:
- Inpaint any overlaid text or graphic elements naturally
- Correct to neutral daylight white balance (5500K)
- Balance exposure: lift shadows +20%, protect highlights
- Boost local micro-contrast on equipment and structural elements
- Reduce yellow/green color cast from artificial indoor lighting
- Apply subtle sharpening to pipes, fixtures, edges, surfaces
- Preserve the installation and work exactly as photographed
Result: clean, consistent quality matching professional real estate
or Houzz portfolio photography.
```

---

## ЗАДАЧА 3: ЄДИНЕ РОЗШИРЕННЯ 1920×1080

### Принцип:
Gemini не завжди точно виводить потрібний розмір — просимо
AI-апскейл + canvas resize після отримання.

### ПРОМПТ 3-A — Апскейл з покращенням деталей:
```
Upscale and enhance this home improvement photo to Full HD
(1920x1080 pixels, 16:9 aspect ratio).

Apply AI upscaling to reconstruct detail:
- Sharpen edges of all equipment, pipes, brackets, and surfaces
- Reduce JPEG compression artifacts and digital noise
- Improve micro-contrast and color clarity throughout
- Recover fine texture details lost in the original compression

Output dimensions: exactly 1920 pixels wide × 1080 pixels tall.
Maintain original composition. Do not crop, add, or remove content.
Preserve realistic appearance of all installed work.
```

### ПРОМПТ 3-B — З очищенням тексту + апскейл:
```
Process this home improvement photo in two steps:

STEP 1 — Clean: Inpaint any overlaid text, captions, or graphic
overlays so background fills naturally without visible traces.

STEP 2 — Upscale to Full HD (1920×1080px): Reconstruct fine detail,
sharpen structural edges and equipment surfaces, reduce compression
artifacts, improve color clarity.

Preserve original composition and all installed work exactly.
Return a single 1920×1080 processed image.
```

### Технічна нотатка:
Після отримання відповіді від API — незалежно від розміру —
canvas.drawImage() ресайзить до точно 1920×1080 зі збереженням
пропорцій і білими полями. Це гарантія єдиного розміру.

---

## ЗАДАЧА 4: ТЕКСТ ПОВЕРХ ФОТО (компанія/брендинг)

### Принцип (офіційний Google Cloud гайд + bananaprompts.com):
- Текст у подвійних лапках: "Company Name"
- Вказуй позицію точно: "bottom-left corner, 8% from edge"
- Описуй шрифт: "bold sans-serif", "clean uppercase"
- Описуй колір і фон тексту: "white text on semi-transparent dark bar"
- Для логотипу + текст — окремий запит після основної обробки

### ПРОМПТ 4-A — Назва компанії + телефон (простий варіант):
```
Add professional contractor branding to this home improvement photo.

In the bottom-left corner, overlay a semi-transparent dark bar
(20% black, spanning full width, height 60px).
Inside this bar, render in clean white sans-serif font:
Left side: "[COMPANY NAME]" — bold, 18px
Right side: "[PHONE NUMBER]" — regular weight, 16px

The overlay must not cover or obscure the main subject of the photo.
Maintain the photo quality and realism exactly as-is.
```

### ПРОМПТ 4-B — Брендинг з позиціонуванням (детальний):
```
Add a professional branding overlay to this contractor photo.

Position: bottom-right corner, 3% margin from edges.
Create a contained badge/label with:
- Background: rounded rectangle, dark charcoal (#1a1a1a), 85% opacity
- Line 1: "[COMPANY NAME]" — bold, white, clean sans-serif, 16px
- Line 2: "[City, State]" — light weight, gray #cccccc, 12px
- Line 3: "[phone or website]" — regular, white, 12px

The badge should look professional and minimal — like a photo credit
on a real estate listing. Do not alter any other part of the photo.
```

### ПРОМПТ 4-C — "Before" / "After" лейбл на фото:
```
Add a clean label to this photo.
Position: top-left corner, 15px from each edge.
Render a pill-shaped badge with:
- Background: solid [#1a5fa8 / #2e7d32] (blue for Before / green for After)
- Text: "[BEFORE / AFTER]" — bold white uppercase sans-serif, 14px
- Padding: 8px horizontal, 5px vertical
- No shadow, no outline — clean flat design

Do not alter the photo content, lighting, or composition in any way.
```

### ПРОМПТ 4-D — Hero page (заголовок поверх фото):
```
Add a professional headline overlay to this home improvement photo
for use as a website hero section.

Create a text block positioned in the lower-left third of the image:
- Semi-transparent background: dark gradient, left-heavy, 70% opacity
- Headline: "[SERVICE TYPE] in [City]" — bold white, 28px, sans-serif
- Subline: "Professional Installation & Service" — 16px, light gray
- CTA text: "Call [PHONE]" — white, 14px, slight letter-spacing

Keep the right portion of the photo clean and unobstructed.
The overlay must look like a professional website hero banner.
Maintain photo quality and realism exactly.
```

---

## КОМБІНОВАНИЙ ПРОМПТ (всі задачі разом)

### Для пакетної обробки — все в одному:
```
Process this home improvement photo in sequence:

1. CLEAN: Inpaint all overlaid text captions and graphic badges —
   fill with natural background texture, no visible traces.

2. STYLE: Apply professional contractor portfolio color grading:
   neutral daylight (5500K), balanced exposure, lifted shadows,
   removed artificial color cast, subtle micro-contrast boost.

3. SHARPEN: Enhance edges of equipment, pipes, surfaces.
   Preserve all installed work exactly as photographed.

4. BRAND: Add a minimal branding bar at bottom:
   Semi-transparent dark overlay, full width, 55px height.
   Left: "[COMPANY NAME]" white bold sans-serif 16px.
   Right: "[PHONE]" white regular 14px.

Return a single polished image ready for contractor portfolio use.
```

---

## ТАБИЦЯ: ЯКУ МОДЕЛЬ КОЛИ ВИКОРИСТОВУВАТИ

| Задача | Модель | Причина |
|--------|--------|---------|
| Очищення тексту | gemini-3.1-flash-image-preview | Менше safety фільтрів |
| До/Після пара | gemini-3.1-flash-image-preview | Краще справляється з парними фото |
| Єдиний стиль серія | gemini-3-pro-image-preview | Точніше слідує color grading інструкціям |
| Апскейл | Будь-яка | Canvas ресайзить після отримання |
| Текст поверх | gemini-3-pro-image-preview | Краще text rendering |
| Великий обсяг | gemini-2.5-flash-image | Найдешевший |

---

## НЕБЕЗПЕЧНІ СЛОВА (тригерять safety фільтр)

| ❌ НЕ ПИСАТИ | ✅ ЗАМІНИТИ НА |
|---|---|
| remove watermark | inpaint overlaid graphics |
| delete logo | clean up graphic overlay |
| erase branding | fill area with background texture |
| remove text | inpaint text caption area |
| destroy / wipe | fill naturally |
| fake / fake photo | retouch / enhance |

---

## ТЕХНІЧНІ ПАРАМЕТРИ ОСВІТЛЕННЯ (для єдиного стилю)

```
5500K         — нейтральний денний стандарт для contractor photos
lift shadows  — підняти тіні без пересвічення
balanced exposure — без blown highlights і pitch-black areas
remove color cast — прибрати жовтий/зелений відтінок
micro-contrast boost — локальне підвищення контрасту
three-point softbox — рівномірне студійне освітлення
```

---

## ПОЗИЦІЇ ДЛЯ ТЕКСТУ

```
top-left corner, 3% from edges
top-right corner, 3% from edges
bottom-left corner, 3% from edges       ← стандарт для contractor
bottom-right corner, 3% from edges
bottom full-width bar, 55px height      ← для назви компанії
top full-width banner, 40px height      ← для before/after лейблу
lower-left third of image               ← для hero page overlay
```
