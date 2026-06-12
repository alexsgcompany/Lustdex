# FOUNDATION.md — фундамент проекту

Це ТЗ для початкового скаффолдингу. Після виконання — зміст буде оновлено.

---

## 1. Що це за проект

Конвеєр обробки даних для мережі тематичних відео-каталогів (adult).
Вхід: партнерські фіди (~2–6 млн відео) + сира семантика + дані GSC.
Вихід: нормалізований каталог у Postgres + посадкові сторінки-кандидати.
Сайти (Astro/Cloudflare) — окремі репозиторії, ЦЕ репо — тільки дані й пайплайн.

Обслуговує одна людина. Все має бути простим, відновлюваним, без магії.

## 2. Жорсткі принципи (для Claude Code — не порушувати)

1. **Пайплайн = детерміновані скрипти + LLM-батчі.** Жодних агентних циклів, жодних "Claude вирішить по ходу". LLM викликається тільки явним batch-скриптом зі структурованим JSON-виходом.
2. **Сирі дані незмінні.** Те, що прийшло з фіда, зберігається as-is (`raw_*` таблиці + `payload jsonb`). Виправлення = мапінг поверх, ніколи не редагування сирого.
3. **Кожен крок пайплайна ідемпотентний.** Повторний запуск не ламає дані і не дублює рядки (upsert / ON CONFLICT). Стан — у БД, не у файлах.
4. **Без ORM.** Чистий SQL через psycopg3. Без SQLAlchemy, без Alembic.
5. **Міграції — нумеровані SQL-файли** в `db/migrations/`, застосовуються скриптом по порядку. Schema-зміни ТІЛЬКИ через нову міграцію, ніколи редагуванням старої.
6. **Нові залежності — тільки після явного погодження.** Не додавати бібліотеку "про запас".
7. **Не створювати файли/модулі, яких нема в структурі нижче,** без погодження. Краще спитати, ніж надумати.
8. **Конфіг — тільки `.env`** (через python-dotenv). Жодних хардкоджених шляхів, ключів, конекшн-стрінгів.
9. **Один скрипт = одна задача**, запускається як `python -m pipeline.<module>.<script>`, пише прогрес у stdout.

## 3. Стек

| Шар | Технологія | Примітка |
|---|---|---|
| БД | Postgres 17 + pgvector | у Docker, єдиний stateful-компонент |
| Мова пайплайна | Python 3.14.3 | venv через `uv`, лінтер `ruff` |
| DB-драйвер | psycopg3 (`psycopg[binary]`) | чистий SQL |
| Fuzzy | rapidfuzz | рівень 2 каскаду тегів |
| Ембединги | sentence-transformers | пізніше, локально на GPU, НЕ в Docker |
| LLM | OpenRouter (DeepSeek) | batch-скрипти, JSON-вихід |
| HTTP/фіди | httpx | |

Python працює **локально у venv**, НЕ в Docker (потрібен доступ до GPU, простіше дебажити). Docker — тільки для Postgres.

## 4. Структура папок

```
.
├── CLAUDE.md               # правила для Claude Code (розділи 2 і 7 цього ТЗ)
├── TODO.md                 # flat list of next steps; check off what's done
├── FOUNDATION.md           # це ТЗ
├── docker-compose.yml
├── .env.example            # шаблон конфіга, .env у .gitignore
├── pyproject.toml          # uv, залежності, ruff-конфіг
├── db/
│   ├── migrations/         # 001_init.sql, 002_*.sql ...
│   └── migrate.py          # застосовує міграції по порядку, веде schema_migrations
├── pipeline/
│   ├── common/             # db.py (конекшн), config.py (.env), log.py
│   ├── ingest/             # парсери фідів: один файл = один провайдер
│   ├── tags/               # каскад нормалізації тегів (перший робочий модуль)
│   ├── embeddings/         # (пізніше) генерація векторів
│   ├── clustering/         # (пізніше) кластеризація семантики
│   ├── llm/                # клієнт OpenRouter + промпти + batch-раннери
│   └── gsc/                # (пізніше) цикл Google Search Console
├── specs/                  # ТЗ на кожен модуль, пишуться ДО коду
│   └── 01-tags.md
├── scripts/                # разові утиліти (дампи, експорт), не частина пайплайна
├── data/                   # .gitignore повністю: сирі фіди, тимчасові файли
└── tests/                  # мінімально: тести normalize() та парсерів
```

Правило для Claude Code: порожні папки (`embeddings/`, `clustering/`, `gsc/`) створити з `.gitkeep`, але НЕ наповнювати, поки нема spec у `specs/`.

## 5. Docker

`docker-compose.yml`:

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    container_name: catalog-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      retries: 10
    shm_size: 1g

  adminer:
    image: adminer
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"
    depends_on:
      - db

volumes:
  pgdata:
```

Порти прив'язані до 127.0.0.1 — назовні нічого не стирчить.

`.env.example`:

```
POSTGRES_USER=catalog
POSTGRES_PASSWORD=change_me
POSTGRES_DB=catalog
DATABASE_URL=postgresql://catalog:change_me@127.0.0.1:5432/catalog
OPENROUTER_API_KEY=
```

## 6. Стартова схема БД (міграція 001_init.sql)

Тільки те, що потрібно для ingest + тегів. Решта — окремими міграціями зі своїми specs.

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE providers (
    id          serial PRIMARY KEY,
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    feed_url    text,
    feed_format text,                      -- 'xml' | 'json' | 'csv'
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- сирі відео: незмінні, повний payload зберігається
CREATE TABLE raw_videos (
    id             bigserial PRIMARY KEY,
    provider_id    int NOT NULL REFERENCES providers(id),
    external_id    text NOT NULL,
    title          text,
    description    text,
    duration_sec   int,
    target_url     text,
    thumb_url      text,
    tags_raw       text[] NOT NULL DEFAULT '{}',
    performers_raw text[] NOT NULL DEFAULT '{}',
    published_at   timestamptz,
    payload        jsonb NOT NULL,
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider_id, external_id)
);

-- канонічний словник тегів
CREATE TABLE tags (
    id         serial PRIMARY KEY,
    slug       text UNIQUE NOT NULL,       -- 'big-tits'
    name       text NOT NULL,              -- 'Big Tits'
    category   text,                       -- 'body' | 'act' | ... (вільний словник)
    status     text NOT NULL DEFAULT 'active',  -- 'active' | 'merged' | 'disabled'
    created_at timestamptz NOT NULL DEFAULT now()
);

-- мапінг: нормалізований сирий тег -> канонічний
CREATE TABLE tag_aliases (
    normalized text PRIMARY KEY,           -- результат normalize(raw)
    tag_id     int NOT NULL REFERENCES tags(id),
    source     text NOT NULL,              -- 'rule' | 'fuzzy' | 'embedding' | 'llm' | 'manual'
    confidence real,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- черга нерозібраних тегів
CREATE TABLE unmapped_tags (
    normalized   text PRIMARY KEY,
    raw_examples text[] NOT NULL DEFAULT '{}',  -- до 5 прикладів сирого написання
    freq         bigint NOT NULL DEFAULT 0,
    status       text NOT NULL DEFAULT 'pending', -- 'pending' | 'trash' | 'resolved'
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
```

Зв'язок відео↔канонічні теги (`video_tags`) — НЕ в цій міграції: він з'явиться разом зі spec модуля, який його наповнює.

## 7. Конвенції коду

- `pipeline/common/db.py` — єдина точка отримання конекшна (`get_conn()` з DATABASE_URL). Усі скрипти ходять через неї.
- Batch-вставки — `executemany` / `COPY`, не по рядку в циклі.
- LLM-промпти — окремими `.md`/`.txt` файлами в `pipeline/llm/prompts/`, не рядками в коді.
- Кожен LLM-батч пише результат у БД з полями `source` і `confidence`.
- Типізація: прості type hints, без pydantic-моделей на кожен чих (виняток — валідація JSON-відповідей LLM).
- Помилки парсингу фіда не валять весь ран: логуються, рядок пропускається, лічильник у підсумку.

## 8. Порядок робіт (кожен крок = окремий PR/коміт)

1. **Скаффолдинг** — це ТЗ: структура, docker-compose, .env.example, pyproject, migrate.py, міграція 001, common/. Без бізнес-логіки.
2. **`specs/01-tags.md` + модуль tags** — функція `normalize()`, наповнення `unmapped_tags` з `raw_videos.tags_raw`, каскад рівнів 1–2 (rules + rapidfuzz), CLI-ревью черги.
3. **Ingest першого провайдера** — один реальний фід, парсер, upsert у `raw_videos`.
4. Далі — рівні 3–4 каскаду (ембединги, LLM), потім семантика/кластеризація. Кожен — своїм spec.

## 9. Definition of Done для скаффолдингу

- `docker compose up -d` → Postgres живий, healthcheck зелений.
- `uv sync` → venv готовий.
- `python -m db.migrate` → міграція 001 застосована, повторний запуск нічого не ламає.
- `python -c "from pipeline.common.db import get_conn; get_conn()"` → конектиться.
- У репо немає жодного файлу поза структурою з розділу 4.