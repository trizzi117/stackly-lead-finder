# Stackly Lead Finder

Находит клиентов в Telegram-чатах по намерению: слушает группы → ловит сообщения
«ищу / нужен …» → AI-классификация (горячий / тёплый / холодный) + готовый первый ответ
→ пуш в Telegram-бот и лента в кабинете.

Два режима на одном движке:
- **Одиночный** (`src/` + `dashboard/`) — для себя: один аккаунт, твои ниши.
- **SaaS** (`app/`) — мульти-тенант: регистрация, кабинет, биллинг, подключение
  аккаунтов клиентов по QR, автономный оркестратор. Это «визитка»-продукт.

---

## SaaS — быстрый старт

> **Куда вводить эти команды?** Это команды для **терминала** (командной строки), а не для браузера или файла.
> На Windows по шагам:
> 1. Установи **Python 3.11+** с python.org — при установке поставь галочку «Add Python to PATH».
> 2. Открой папку `stackly-lead-finder` в Проводнике.
> 3. В адресной строке Проводника (сверху, где путь) набери `powershell` и нажми Enter — откроется чёрное окно терминала уже в этой папке.
> 4. Вводи команды ниже **по одной**, каждую заверши клавишей Enter и дождись завершения.
>
> Строки после `#` — это комментарии-подсказки, их вводить не нужно.
>
> ⚠️ В PowerShell команды нельзя соединять через `&&` («не является допустимым разделителем операторов») — вводи каждую команду **отдельной строкой**, по одной. И `source` в Windows нет: активация venv там — `.venv\Scripts\activate`.

**Windows (PowerShell) — по одной строке:**

```powershell
pip install -r requirements.txt

copy .env.example .env
#   открой .env блокнотом и впиши ключи: TG_API_ID/TG_API_HASH (my.telegram.org),
#   PLATFORM_BOT_TOKEN + PLATFORM_BOT_USERNAME (@BotFather),
#   LLM_API_KEY (DeepSeek/OpenAI), SESSION_SECRET (любая длинная строка)

python -m app.seed                        # демо-аккаунт: demo@stackly.local / demo123
python -m uvicorn app.main:app --reload   # сайт + кабинет → http://127.0.0.1:8000

# боевой режим: в ОТДЕЛЬНОМ окне терминала — автономные сервисы:
python -m app.run_all
```

> Виртуальное окружение (venv) — по желанию, для чистоты. Не обязательно, чтобы просто запустить.
> Если хочешь: `python -m venv .venv`, затем активация `.venv\Scripts\activate` (Linux/Mac: `source .venv/bin/activate`).

**Linux / Mac:**

```bash
pip install -r requirements.txt
cp .env.example .env        # заполни ключи (см. выше)
python -m app.seed
python -m uvicorn app.main:app --reload
python -m app.run_all       # в отдельном терминале
```

Открой сайт → «Начать бесплатно» → кабинет: подключи Telegram по QR, синхронизируй
чаты, задай ключевые слова → оркестратор сам начнёт ловить лидов.

### Маршруты
Сайт: `/` `/features` `/pricing` `/guide` `/faq` `/privacy`
Кабинет: `/app` (лиды) `/app/stats` `/app/contacts` `/app/chats` `/app/keywords`
`/app/connect` (TG по QR) `/app/bot` (привязка бота) `/app/billing` `/app/settings`

---

## Архитектура SaaS

```
Браузер ── FastAPI (app/main.py) ── Jinja2 (сайт + кабинет)
                       │
                       ├─ auth.py        регистрация/логин (pbkdf2)
                       ├─ billing.py     тарифы, лимиты, триал (провайдер-агностик)
                       ├─ tg_connect.py  вход в Telegram по QR (StringSession в БД)
                       └─ models.py      User/Subscription/TgAccount/Lead/Chat (SQLAlchemy)

app/orchestrator.py  ── на каждого активного тенанта поднимает Telethon-монитор
   по его чатам/ключам → src.prefilter → src.classifier (LLM) → Lead в БД
   → платформенный бот шлёт горячие в личку клиента.  Работает «само по себе».

app/bot.py  ── привязка лички (/start <token>) + канал доставки лидов/биллинга.
```

Один движок: `src/prefilter`, `src/classifier`, `src/llm` переиспользуются и в
одиночном режиме, и в SaaS-оркестраторе.

---

## ⚠️ Ban-playbook (ядро бизнеса)

Аккаунты банят за «ботообразное» поведение, не за чтение. Правила:
1. Только **вторичные прогретые** аккаунты, не основные.
2. **Никакой авто-отправки** — клиент отвечает руками (так и сделано).
3. Вступать в чаты **медленно** (10–20/день), уважать `FLOOD_WAIT`.
4. Одна стабильная сессия + один IP/гео на аккаунт.
5. На масштабе — резидентные прокси + ротация (отдельный слой Phase 2+).

---

## Биллинг

Провайдер-агностик (`app/billing.py`). По умолчанию `BILLING_PROVIDER=manual` —
тариф активируется вручную/автотестово, всё работает локально. Для приёма денег
физлицу/самозанятому в РФ: подключить **Lava** или **Cryptomus** (вебхук →
`/billing/webhook/{provider}` → `handle_webhook`). Stripe в РФ не работает; ЮKassa
требует регистрации бизнеса.

---

## Структура

```
stackly-lead-finder/
├─ app/                 SaaS: main, db, models, auth, billing, tg_connect,
│  │                    orchestrator, bot, platform_bot, seed, run_all
│  ├─ templates/        Jinja2: marketing/ app/ auth/ partials/
│  └─ static/app.css    дизайн-система
├─ src/                 движок (общий): prefilter, classifier, llm, notifier, …
├─ dashboard/           одиночный дашборд (FastAPI)
├─ landing/index.html   статичный лендинг (для GitHub Pages)
├─ config/  scripts/  tests/  data/
└─ requirements.txt   .env.example
```

---

## Проверка (когда вернётся песочница)

```bash
python tests/test_prefilter.py          # ядро фильтрации
python -m app.seed && uvicorn app.main:app   # открыть кабинет на демо-данных
```

Полный стратегический разбор — `../LeadGen_Чертёж.md`.
