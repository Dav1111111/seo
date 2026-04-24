# Grand Tour Spirit — Что осталось доделать

**Дата проверки**: 17 апреля 2026

## Что уже работает (проверено)

- [x] robots.txt — отдаётся как text/plain, содержит Disallow для /admin, /login, /portal, ссылка на sitemap
- [x] sitemap.xml — 9 URL, формат XML
- [x] non-www → www редирект (301)
- [x] `<html lang="ru">`
- [x] manifest.json — "Grand Tour Spirit", "GTS", правильное описание
- [x] Главная `/` — уникальный title, description, canonical, OG, Schema.org
- [x] `/abkhazia` — уникальный title, description, canonical, OG, Schema.org
- [x] `/experiences` — уникальный title, description, canonical, OG, Schema.org
- [x] `/stories` — уникальный title, description, canonical, OG, Schema.org
- [x] `/contacts` — уникальный title, description, canonical, OG, Schema.org
- [x] `/partners` — уникальный title, description, canonical, OG
- [x] Все 6 основных страниц возвращают разный HTML (не дубликаты)

---

## Что НЕ работает — нужно исправить

### 1. Блог-статьи отдают мета-теги главной страницы

**Проблема**: Все URL вида `/stories/{slug}` получают мета-теги от главной страницы вместо своих уникальных.

**Текущее состояние** (одинаковое для всех трёх статей):
```
/stories/seo-abkhazia-ritza
/stories/seo-abkhazia-excursions  
/stories/seo-abkhazia-top15

title:     "Grand Tour Spirit — Премиальный клуб активного отдыха в Сочи"  ← НЕПРАВИЛЬНО
desc:      "Багги-экспедиции по Абхазии, яхт-туры..."                      ← НЕПРАВИЛЬНО
canonical: "https://www.grandtourspirit.ru/"                                 ← НЕПРАВИЛЬНО (указывает на главную!)
og:title:  "Grand Tour Spirit — Премиальный клуб активного отдыха в Сочи"  ← НЕПРАВИЛЬНО
```

**Почему**: nginx `sub_filter` скорее всего матчит только точные пути (`/`, `/abkhazia` и т.д.), но не покрывает `/stories/*` маршруты.

**Как исправить**: Два варианта:

**Вариант A (если статей мало — до 20)**:
Добавить в nginx отдельный `location` блок для каждой статьи:

```nginx
location = /stories/seo-abkhazia-ritza {
    # sub_filter для подмены title/description
    sub_filter '<title>Grand Tour Spirit</title>' 
               '<title>Озеро Рица — полный путеводитель 2026 | Grand Tour Spirit</title>';
    sub_filter '<meta name="description" content="Багги-экспедиции по Абхазии...'
               '<meta name="description" content="Озеро Рица в Абхазии: как добраться из Сочи, что посмотреть, лучшее время для посещения. Подробный гид с фото и советами."';
    sub_filter 'rel="canonical" href="https://www.grandtourspirit.ru/"'
               'rel="canonical" href="https://www.grandtourspirit.ru/stories/seo-abkhazia-ritza"';
    # ... аналогично og:title, og:url
    try_files $uri /index.html;
}
```

**Вариант B (если статей будет много — рекомендуемый)**:
Динамическая подстановка через Lua-модуль nginx или через middleware на Node.js, которое читает мета-данные статьи из Supabase и подставляет в HTML.

**Конкретные мета-теги для существующих статей:**

Статья: `/stories/seo-abkhazia-ritza`
```
Title: Озеро Рица — полный путеводитель: как добраться, что посмотреть | GTS
Description: Озеро Рица в Абхазии: как добраться из Сочи, маршруты, стоимость, лучшее время для посещения. Подробный гид с фото и практическими советами.
Canonical: https://www.grandtourspirit.ru/stories/seo-abkhazia-ritza
```

Статья: `/stories/seo-abkhazia-excursions`
```
Title: Экскурсии в Абхазию из Сочи 2026 — все варианты и цены | GTS
Description: Полный обзор экскурсий в Абхазию из Сочи: автобусные, джиповые, на багги. Маршруты, цены, что включено, советы по документам.
Canonical: https://www.grandtourspirit.ru/stories/seo-abkhazia-excursions
```

Статья: `/stories/seo-abkhazia-top15`
```
Title: Топ-15 мест в Абхазии — что посмотреть туристу | GTS
Description: 15 лучших достопримечательностей Абхазии: от озера Рица и Нового Афона до секретных мест. Гид с фото, картой и советами.
Canonical: https://www.grandtourspirit.ru/stories/seo-abkhazia-top15
```

---

### 2. Schema.org отсутствует на странице /partners

**Проблема**: На всех основных страницах есть JSON-LD разметка, но на `/partners` — нет.

**Как исправить**: Добавить Schema.org блок для /partners:

```json
{
  "@context": "https://schema.org",
  "@type": "WebPage",
  "name": "Партнёрская программа Grand Tour Spirit",
  "description": "Сотрудничество с агрегатором премиальных впечатлений Сочи",
  "url": "https://www.grandtourspirit.ru/partners",
  "publisher": {
    "@type": "TravelAgency",
    "name": "Grand Tour Spirit"
  }
}
```

---

### 3. SSR / Pre-rendering — контент статей невидим для Яндекса

**Проблема**: Тело HTML страниц содержит JavaScript-код, но не содержит видимого текста на русском языке. Это значит контент статей (текст, заголовки, абзацы) рендерится только в браузере, а поисковый робот Яндекса его не видит.

**Что видит Яндекс сейчас**:
- title и description (благодаря sub_filter) ← это хорошо
- Но текст самой статьи — НЕ видит ← это проблема

**Что нужно**: Текст статьи должен быть в HTML ДО выполнения JavaScript. 

**Варианты решения (от простого к сложному):**

**Вариант A — react-snap (1-2 дня)**:
Инструмент запускает Chrome при сборке и сохраняет отрендеренный HTML каждой страницы как статический файл. Простое решение, но не работает для динамических страниц (новые статьи добавляемые через CMS не будут pre-rendered до следующей сборки).

**Вариант B — Prerender.io или аналог (1 день настройки)**:
Сервис-прослойка: если запрос от бота (Yandexbot, Googlebot), перенаправляет на сервер рендеринга, который возвращает полный HTML. Обычные пользователи получают SPA как раньше. Есть бесплатные self-hosted варианты.

**Вариант C — Vike SSR (2-4 недели)**:
Полноценный server-side rendering. Самый правильный вариант, но требует переписывания роутера и настройки Node.js сервера.

**Рекомендация**: Начните с варианта B (prerender для ботов) — это решает проблему индексации за 1 день, а SSR можно делать потом.

---

### 4. Новые статьи — добавлять мета-теги для каждой

**При публикации каждой новой статьи в блоге** нужно:
1. Добавить URL в sitemap.xml
2. Добавить мета-теги (title, description, canonical, OG) через nginx или middleware
3. Проверить что статья доступна с уникальными мета-тегами

Если статей будет 20+, ручное добавление в nginx станет неудобным — нужно автоматизировать через middleware или CMS-хуки.

---

## Чек-лист для проверки после исправлений

Для каждой статьи `/stories/{slug}` проверить:
- [ ] title содержит название статьи (НЕ "Grand Tour Spirit — Премиальный клуб...")
- [ ] description уникальный и описывает содержание статьи
- [ ] canonical указывает на URL статьи (НЕ на главную `/`)
- [ ] og:title = title статьи
- [ ] og:url = URL статьи
- [ ] Текст статьи виден в HTML без выполнения JavaScript (curl URL | grep "первые слова статьи")

Для /partners:
- [ ] Есть JSON-LD Schema.org блок

Команда проверки (выполнить в терминале):
```bash
# Проверить мета-теги статьи
curl -s https://www.grandtourspirit.ru/stories/seo-abkhazia-ritza | grep -oP '<title>\K[^<]+'

# Проверить canonical
curl -s https://www.grandtourspirit.ru/stories/seo-abkhazia-ritza | grep -oP 'canonical.*?href="\K[^"]+'

# Проверить что текст статьи виден в HTML (без JS)
curl -s https://www.grandtourspirit.ru/stories/seo-abkhazia-ritza | grep -c '[А-Яа-я]\{30,\}'
# Результат > 0 = текст есть, 0 = текст не виден
```
