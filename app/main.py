"""Stackly Lead Finder — SaaS web-приложение (FastAPI + Jinja2).

Запуск:  uvicorn app.main:app --reload   →  http://127.0.0.1:8000
Перед первым запуском:  python -m app.seed   (создаёт БД + демо-аккаунт)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import segno

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import SearchRequest as TgSearchRequest
from telethon.tl.types import Channel, Chat
from telethon.utils import get_peer_id

from . import billing, platform_bot, tg_connect
from .auth import authenticate, create_user, email_taken
from .db import get_db, init_db
from .models import Lead, MonitoredChat, TgAccount, User, utcnow
from src.classifier import Classifier
from src.llm import LLM
from src.prefilter import PreFilter

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals["METRIKA_ID"] = os.getenv("METRIKA_ID", "").strip()

app = FastAPI(title="Stackly Lead Finder")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev-secret-change-me"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ── helpers ─────────────────────────────────────────────────────────────
def get_user(request: Request, db: Session) -> User | None:
    uid = request.session.get("uid")
    return db.get(User, uid) if uid else None


def render(request: Request, name: str, user: User | None = None, **extra):
    ctx = {"request": request, "user": user, "now": utcnow(),
           "PLANS": billing.PLANS, "PLAN_ORDER": billing.PLAN_ORDER}
    if user:
        ctx["sub"] = user.subscription
        ctx["limits"] = billing.limits(user.subscription)
    ctx.update(extra)
    templates.env.globals.setdefault("plan_title", lambda p: billing.PLANS.get(p, {}).get("title", p))
    return templates.TemplateResponse(name, ctx)


def need_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def _qr_img(url: str | None) -> str:
    if not url:
        return ""
    try:
        return segno.make(url, error="m").png_data_uri(scale=6, border=2)
    except Exception:  # noqa: BLE001
        return ""


# ── marketing ───────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    return render(request, "marketing/index.html", get_user(request, db))

@app.get("/features", response_class=HTMLResponse)
def features(request: Request, db: Session = Depends(get_db)):
    return render(request, "marketing/features.html", get_user(request, db))

@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request, db: Session = Depends(get_db)):
    return render(request, "marketing/pricing.html", get_user(request, db))

@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request, db: Session = Depends(get_db)):
    return render(request, "marketing/guide.html", get_user(request, db))

@app.get("/faq", response_class=HTMLResponse)
def faq(request: Request, db: Session = Depends(get_db)):
    return render(request, "marketing/faq.html", get_user(request, db))

@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request, db: Session = Depends(get_db)):
    return render(request, "marketing/privacy.html", get_user(request, db))


# ── auth ────────────────────────────────────────────────────────────────
@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request, db: Session = Depends(get_db)):
    if get_user(request, db):
        return RedirectResponse("/app", status_code=303)
    return render(request, "auth/register.html", None)

@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...),
             db: Session = Depends(get_db)):
    email = email.strip().lower()
    if len(password) < 6:
        return render(request, "auth/register.html", None, error="Пароль минимум 6 символов", email=email)
    if email_taken(db, email):
        return render(request, "auth/register.html", None, error="Email уже зарегистрирован", email=email)
    user = create_user(db, email, password)
    billing.start_trial(db, user)  # 5 дней бесплатно сразу
    request.session["uid"] = user.id
    return RedirectResponse("/app", status_code=303)

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db)):
    if get_user(request, db):
        return RedirectResponse("/app", status_code=303)
    return render(request, "auth/login.html", None)

@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          db: Session = Depends(get_db)):
    user = authenticate(db, email, password)
    if not user:
        return render(request, "auth/login.html", None, error="Неверный email или пароль", email=email)
    request.session["uid"] = user.id
    return RedirectResponse("/app", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ── stats helper ────────────────────────────────────────────────────────
def compute_stats(db: Session, uid: int) -> dict:
    q = db.query(Lead).filter(Lead.user_id == uid)
    total = q.count()
    today = datetime.now(timezone.utc).date()
    today_n = q.filter(func.date(Lead.created_at) == today.isoformat()).count()
    by_class = dict(db.query(Lead.classification, func.count()).filter(Lead.user_id == uid)
                    .group_by(Lead.classification).all())
    by_status = dict(db.query(Lead.status, func.count()).filter(Lead.user_id == uid)
                     .group_by(Lead.status).all())
    by_day = db.query(func.date(Lead.created_at), func.count()).filter(Lead.user_id == uid)\
        .group_by(func.date(Lead.created_at)).order_by(func.date(Lead.created_at).desc()).limit(14).all()
    top_kw = db.query(Lead.keyword, func.count()).filter(Lead.user_id == uid, Lead.keyword.isnot(None))\
        .group_by(Lead.keyword).order_by(func.count().desc()).limit(8).all()
    top_chats = db.query(Lead.chat_title, func.count()).filter(Lead.user_id == uid)\
        .group_by(Lead.chat_title).order_by(func.count().desc()).limit(8).all()
    return {
        "total": total, "today": today_n,
        "hot": by_class.get("hot", 0), "warm": by_class.get("warm", 0), "cold": by_class.get("cold", 0),
        "by_status": by_status,
        "by_day": [{"day": d, "n": n} for d, n in reversed(by_day)],
        "top_keywords": [{"keyword": k, "n": n} for k, n in top_kw],
        "top_chats": [{"chat": c, "n": n} for c, n in top_chats],
    }


# ── cabinet ─────────────────────────────────────────────────────────────
@app.get("/app", response_class=HTMLResponse)
def dashboard(request: Request, classification: str = "all", status: str = "all",
              db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    q = db.query(Lead).filter(Lead.user_id == user.id)
    if classification != "all":
        q = q.filter(Lead.classification == classification)
    if status != "all":
        q = q.filter(Lead.status == status)
    leads = q.order_by(Lead.created_at.desc()).limit(200).all()
    acc_connected = bool(user.tg_account and user.tg_account.status == "connected")
    active_chats = db.query(MonitoredChat).filter(
        MonitoredChat.user_id == user.id, MonitoredChat.active.is_(True)).count()
    has_keywords = len(user.get_keywords()) > 0
    return render(request, "app/dashboard.html", user, leads=leads, stats=compute_stats(db, user.id),
                  cur_class=classification, cur_status=status, active="dashboard",
                  acc_connected=acc_connected, active_chats=active_chats, has_keywords=has_keywords)

@app.get("/app/stats", response_class=HTMLResponse)
def stats_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    return render(request, "app/stats.html", user, stats=compute_stats(db, user.id), active="stats")

@app.get("/app/contacts", response_class=HTMLResponse)
def contacts_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    rows = db.query(
        Lead.sender_name, Lead.username, Lead.sender_id,
        func.count().label("n"), func.max(Lead.created_at).label("last"),
        func.max(Lead.status).label("status"),
    ).filter(Lead.user_id == user.id).group_by(Lead.sender_id).order_by(func.max(Lead.created_at).desc()).limit(200).all()
    return render(request, "app/contacts.html", user, contacts=rows, active="contacts")

@app.post("/app/leads/{lead_id}/status")
def lead_status(lead_id: int, request: Request, status: str = Form(...), db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user.id).first()
    if lead and status in {"new", "contacted", "converted", "dismissed"}:
        lead.status = status
        db.commit()
    return RedirectResponse(request.headers.get("referer", "/app"), status_code=303)


# ── settings / keywords ─────────────────────────────────────────────────
@app.get("/app/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    return render(request, "app/settings.html", user, active="settings", saved=request.query_params.get("saved"))

@app.post("/app/settings")
def settings_save(request: Request, business_context: str = Form(""), hot_threshold: int = Form(70),
                  db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    user.business_context = business_context.strip()
    user.hot_threshold = max(0, min(100, hot_threshold))
    db.commit()
    return RedirectResponse("/app/settings?saved=1", status_code=303)

@app.get("/app/keywords", response_class=HTMLResponse)
def keywords_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    return render(request, "app/keywords.html", user, active="keywords",
                  keywords="\n".join(user.get_keywords()), stop_words="\n".join(user.get_stop_words()),
                  business_context=user.business_context, hot_threshold=user.hot_threshold,
                  saved=request.query_params.get("saved"))

@app.post("/app/keywords")
def keywords_save(request: Request, keywords: str = Form(""), stop_words: str = Form(""),
                  business_context: str = Form(""), hot_threshold: int = Form(70),
                  db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    user.set_keywords([k.strip() for k in keywords.splitlines() if k.strip()])
    user.set_stop_words([s.strip() for s in stop_words.splitlines() if s.strip()])
    user.business_context = business_context.strip()
    user.hot_threshold = max(0, min(100, hot_threshold))
    db.commit()
    return RedirectResponse("/app/keywords?saved=1", status_code=303)


# ── Telegram account (QR connect) ───────────────────────────────────────
@app.get("/app/connect", response_class=HTMLResponse)
def connect_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    return render(request, "app/connect.html", user, active="connect", acc=user.tg_account)

@app.post("/app/connect/start")
async def connect_start(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return JSONResponse({"status": "error", "error": "auth"}, status_code=401)
    cs = await tg_connect.start_qr(user.id)
    acc = user.tg_account or TgAccount(user_id=user.id)
    if acc.id is None:
        db.add(acc)
    acc.status = "connecting"
    db.commit()
    return JSONResponse({"status": cs.status, "qr_url": cs.qr_url,
                         "qr_img": _qr_img(cs.qr_url), "error": cs.error})

@app.get("/app/connect/status")
async def connect_status(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return JSONResponse({"status": "error"}, status_code=401)
    cs = tg_connect.get(user.id)
    if not cs:
        return JSONResponse({"status": "idle"})
    if cs.status == "connected":
        done = tg_connect.pop_if_connected(user.id)
        acc = user.tg_account or TgAccount(user_id=user.id)
        if acc.id is None:
            db.add(acc)
        acc.session_string = done.session_string
        acc.tg_user_id = done.tg_user_id
        acc.username = done.username
        acc.phone = done.phone
        acc.status = "connected"
        acc.connected_at = utcnow()
        db.commit()
        return JSONResponse({"status": "connected", "username": done.username})
    return JSONResponse({"status": cs.status, "qr_url": cs.qr_url,
                         "qr_img": _qr_img(cs.qr_url), "error": cs.error})

@app.post("/app/connect/password")
async def connect_password(request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return JSONResponse({"status": "error"}, status_code=401)
    cs = await tg_connect.submit_password(user.id, password)
    return JSONResponse({"status": cs.status if cs else "idle", "error": cs.error if cs else ""})


async def _sync_chats(user: User, db: Session) -> int:
    acc = user.tg_account
    if not acc or not acc.session_string:
        return 0
    client = TelegramClient(StringSession(acc.session_string),
                            tg_connect.API_ID, tg_connect.API_HASH)
    added = 0
    await client.connect()
    try:
        async for dialog in client.iter_dialogs():
            ent = dialog.entity
            if not isinstance(ent, (Channel, Chat)):
                continue
            exists = db.query(MonitoredChat).filter(
                MonitoredChat.user_id == user.id, MonitoredChat.chat_id == dialog.id).first()
            if exists:
                exists.title = dialog.name or exists.title
                continue
            db.add(MonitoredChat(user_id=user.id, chat_id=dialog.id, title=dialog.name or "",
                                 username=getattr(ent, "username", None),
                                 is_channel=bool(getattr(ent, "broadcast", False)), active=False))
            added += 1
        db.commit()
    finally:
        await client.disconnect()
    return added


@app.get("/app/chats", response_class=HTMLResponse)
def chats_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    chats = db.query(MonitoredChat).filter(MonitoredChat.user_id == user.id)\
        .order_by(MonitoredChat.active.desc(), MonitoredChat.title).all()
    active_n = sum(1 for c in chats if c.active)
    return render(request, "app/chats.html", user, active="chats", chats=chats, active_n=active_n,
                  acc=user.tg_account, msg=request.query_params.get("msg"))

@app.post("/app/chats/sync")
async def chats_sync(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    try:
        n = await _sync_chats(user, db)
        msg = f"Синхронизировано, добавлено новых: {n}"
    except Exception as exc:  # noqa: BLE001
        msg = f"Ошибка синка: {exc}"
    return RedirectResponse(f"/app/chats?msg={msg}", status_code=303)

@app.post("/app/chats/{chat_id}/toggle")
def chat_toggle(chat_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    chat = db.query(MonitoredChat).filter(MonitoredChat.id == chat_id, MonitoredChat.user_id == user.id).first()
    if chat:
        if not chat.active and not billing.can_add_chat(db, user):
            return RedirectResponse("/app/chats?msg=Достигнут лимит чатов по тарифу", status_code=303)
        chat.active = not chat.active
        db.commit()
    return RedirectResponse("/app/chats", status_code=303)


@app.post("/app/chats/scan-history")
async def scan_history(request: Request, limit: int = Form(300), db: Session = Depends(get_db)):
    """Разовый проход по истории активных чатов: фильтр → ИИ → лиды. С лимитами."""
    user = get_user(request, db)
    if not user:
        return need_login()
    keywords = user.get_keywords()
    if not keywords:
        return RedirectResponse("/app/chats?msg=Сначала задайте ключевые слова (Настройка поиска)", status_code=303)
    active = db.query(MonitoredChat).filter(
        MonitoredChat.user_id == user.id, MonitoredChat.active.is_(True)).all()
    if not active:
        return RedirectResponse("/app/chats?msg=Включите хотя бы один чат", status_code=303)
    llm = _llm_or_none()
    if not llm:
        return RedirectResponse("/app/chats?msg=Нужен LLM-ключ в .env", status_code=303)
    client = await _open_user_client(user)
    if not client:
        return RedirectResponse("/app/chats?msg=Сначала подключите Telegram-аккаунт", status_code=303)

    limit = max(50, min(int(limit), 1000))
    prefilter = PreFilter(keywords, user.get_stop_words())
    classifier = Classifier(llm, user.business_context)
    AI_CAP = 30  # потолок ИИ-оценок за один скан — бережём бесплатный лимит OpenRouter
    found, ai_calls = 0, 0
    try:
        for chat in active:
            if chat.is_channel:  # односторонний канал — сообщений от людей нет, пропускаем
                continue
            if ai_calls >= AI_CAP:
                break
            try:
                async for msg in client.iter_messages(chat.chat_id, limit=limit):
                    text = msg.message or ""
                    if not text.strip():
                        continue
                    kw = prefilter.match(text)
                    if not kw:
                        continue
                    if ai_calls >= AI_CAP:
                        break
                    result = await classifier.classify(text, chat.title)
                    ai_calls += 1
                    sender = None
                    try:
                        sender = await msg.get_sender()
                    except Exception:  # noqa: BLE001
                        pass
                    uname = getattr(sender, "username", None)
                    sname = " ".join(p for p in [getattr(sender, "first_name", None),
                                                 getattr(sender, "last_name", None)] if p) or (uname or "—")
                    link = (f"https://t.me/{chat.username}/{msg.id}" if chat.username
                            else (f"https://t.me/{uname}" if uname else None))
                    try:
                        db.add(Lead(user_id=user.id, created_at=utcnow(), chat_id=chat.chat_id,
                                    chat_title=chat.title, message_id=msg.id, sender_id=msg.sender_id,
                                    sender_name=sname, username=uname, text=text, keyword=kw,
                                    classification=result.classification, score=result.score,
                                    intent=result.intent, reply=result.reply, status="new", link=link))
                        db.commit()
                        found += 1
                    except IntegrityError:
                        db.rollback()  # уже есть такой лид
                    await asyncio.sleep(0.3)
            except Exception:  # noqa: BLE001
                continue
    finally:
        await client.disconnect()
    return RedirectResponse(
        f"/app/chats?msg=Скан истории готов: добавлено {found} лидов (ИИ-оценок {ai_calls}/{AI_CAP})",
        status_code=303)


@app.post("/app/chats/{chat_id}/similar")
async def chat_similar(chat_id: int, request: Request, db: Session = Depends(get_db)):
    """ИИ подбирает похожие публичные чаты/каналы для расширения охвата."""
    user = get_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Не авторизован"}, status_code=401)
    if not billing.limits(user.subscription).get("channel_search"):
        return JSONResponse({"ok": False, "message": "«Найти похожие» доступно на тарифах Про и выше."})
    chat = db.query(MonitoredChat).filter(MonitoredChat.id == chat_id, MonitoredChat.user_id == user.id).first()
    if not chat:
        return JSONResponse({"ok": False, "message": "Чат не найден"})
    client = await _open_user_client(user)
    if not client:
        return JSONResponse({"ok": False, "message": "Сначала подключите Telegram-аккаунт."})
    queries = [chat.title] if chat.title else []
    llm = _llm_or_none()
    if llm:
        try:
            data = _parse_json(await llm.complete(
                'Верни СТРОГО JSON {"queries":[5 коротких поисковых запросов для Telegram-ГРУПП, '
                'похожих по теме на этот чат и подходящих бизнесу]}.',
                f"Чат: {chat.title}\nБизнес: {user.business_context or 'услуги для малого бизнеса'}",
                json_mode=True))
            queries += [str(q).strip() for q in data.get("queries", []) if str(q).strip()][:5]
        except Exception:  # noqa: BLE001
            pass
    if not queries:
        await client.disconnect()
        return JSONResponse({"ok": False, "message": "Не удалось собрать запрос для поиска."})
    try:
        results = await _search_tg(client, queries)
    finally:
        await client.disconnect()
    # убираем то, что уже в списке мониторинга
    existing = {row.chat_id for row in
                db.query(MonitoredChat.chat_id).filter(MonitoredChat.user_id == user.id).all()}
    results = [r for r in results if r["chat_id"] not in existing]
    return JSONResponse({"ok": True, "source": chat.title, "results": results})


# ── Поиск каналов (discovery): простой + ИИ-поиск ───────────────────────
async def _open_user_client(user: User):
    acc = user.tg_account
    if not acc or not acc.session_string:
        return None
    client = TelegramClient(StringSession(acc.session_string), tg_connect.API_ID, tg_connect.API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return None
    return client


def _chat_dict(ch) -> dict:
    return {
        "chat_id": get_peer_id(ch),
        "title": getattr(ch, "title", "") or "",
        "username": getattr(ch, "username", None),
        "is_channel": bool(getattr(ch, "broadcast", False)),
        "participants": getattr(ch, "participants_count", None),
    }


async def _search_tg(client, queries: list[str], limit: int = 20) -> list[dict]:
    seen: set[int] = set()
    out: list[dict] = []
    for q in [x.strip() for x in queries if x.strip()][:6]:
        try:
            res = await client(TgSearchRequest(q=q, limit=limit))
        except Exception:  # noqa: BLE001
            continue
        for ch in res.chats:
            # только ГРУППЫ/беседы, где пишут люди: супергруппа (megagroup) или старая группа.
            # односторонние broadcast-каналы пропускаем — лидов там нет.
            is_group = isinstance(ch, Chat) or (isinstance(ch, Channel) and getattr(ch, "megagroup", False))
            if not is_group:
                continue
            cid = get_peer_id(ch)
            if cid in seen:
                continue
            seen.add(cid)
            out.append(_chat_dict(ch))
    return out


def _llm_or_none():
    key = os.getenv("LLM_API_KEY", "")
    if not key:
        return None
    return LLM(os.getenv("LLM_BASE_URL", "https://api.deepseek.com"), key, os.getenv("LLM_MODEL", "deepseek-chat"))


def _parse_json(raw: str) -> dict:
    return json.loads(raw) if raw.strip().startswith("{") else json.loads(re.search(r"\{.*\}", raw, re.S).group(0))


@app.get("/app/discovery", response_class=HTMLResponse)
def discovery_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    return render(request, "app/discovery.html", user, active="discovery", acc=user.tg_account)


@app.post("/app/discovery/simple")
async def discovery_simple(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Не авторизован"}, status_code=401)
    body = await request.json()
    queries = [q.strip() for q in re.split(r"[,\n]", body.get("queries", "")) if q.strip()]
    if not queries:
        return JSONResponse({"ok": False, "message": "Введите хотя бы одну фразу"})
    client = await _open_user_client(user)
    if not client:
        return JSONResponse({"ok": False, "message": "Сначала подключите Telegram-аккаунт (раздел «Telegram-аккаунт»)."})
    try:
        results = await _search_tg(client, queries)
    finally:
        await client.disconnect()
    return JSONResponse({"ok": True, "results": results})


@app.post("/app/discovery/ai-keys")
async def discovery_ai_keys(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    body = await request.json()
    business = (body.get("business") or user.business_context or "").strip()
    if not business:
        return JSONResponse({"ok": False, "message": "Опишите бизнес в пару предложений"})
    llm = _llm_or_none()
    if not llm:
        return JSONResponse({"ok": False, "message": "Нужен LLM-ключ в .env"})
    system = ('Ты настраиваешь поиск B2B-клиентов в Telegram. Верни СТРОГО JSON: '
              '{"keywords":[7 фраз намерения покупателя],"stop_words":[6 шумовых слов],'
              '"queries":[6 запросов для поиска Telegram-каналов и групп]}.')
    try:
        data = _parse_json(await llm.complete(system, f"Бизнес: {business}", json_mode=True))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "message": f"ИИ не ответил: {exc}"})
    return JSONResponse({"ok": True, "keywords": data.get("keywords", []),
                         "stop_words": data.get("stop_words", []), "queries": data.get("queries", [])})


@app.post("/app/discovery/ai-search")
async def discovery_ai_search(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    body = await request.json()
    queries = [q.strip() for q in body.get("queries", []) if q.strip()]
    business = (body.get("business") or user.business_context or "").strip()
    if not queries:
        return JSONResponse({"ok": False, "message": "Отметьте хотя бы один запрос"})
    client = await _open_user_client(user)
    if not client:
        return JSONResponse({"ok": False, "message": "Сначала подключите Telegram-аккаунт."})
    try:
        results = await _search_tg(client, queries)
    finally:
        await client.disconnect()
    llm = _llm_or_none()
    if llm and results:
        try:
            listing = "\n".join(f"{i}. {r['title']}" for i, r in enumerate(results))
            system = ('Оцени релевантность каждого Telegram-сообщества для бизнеса (0-100: где сидят '
                      'потенциальные клиенты). Верни СТРОГО JSON: {"scores":[{"i":0,"score":85}]}.')
            data = _parse_json(await llm.complete(system, f"Бизнес: {business}\nСписок:\n{listing}", json_mode=True))
            smap = {int(s["i"]): max(0, min(100, int(s["score"]))) for s in data.get("scores", [])}
            for i, r in enumerate(results):
                r["relevance"] = smap.get(i)
            results.sort(key=lambda r: (r.get("relevance") or 0), reverse=True)
        except Exception:  # noqa: BLE001
            pass
    return JSONResponse({"ok": True, "results": results})


@app.post("/app/discovery/apply")
async def discovery_apply(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    body = await request.json()
    items = body.get("items", [])[:15]
    if not items:
        return JSONResponse({"ok": False, "message": "Ничего не выбрано"})
    client = await _open_user_client(user)
    if not client:
        return JSONResponse({"ok": False, "message": "Сначала подключите Telegram-аккаунт."})
    added = 0
    try:
        for it in items:
            uname = it.get("username")
            try:
                cid = int(it.get("chat_id"))
            except (TypeError, ValueError):
                cid = None
            if uname:  # публичные — вступаем по username, медленно (ban-safety)
                try:
                    ent = await client.get_entity(uname)
                    await client(JoinChannelRequest(ent))
                    cid = get_peer_id(ent)
                except Exception:  # noqa: BLE001
                    pass
            if cid is None:
                continue
            exists = db.query(MonitoredChat).filter(
                MonitoredChat.user_id == user.id, MonitoredChat.chat_id == cid).first()
            if exists:
                exists.active = True
            else:
                db.add(MonitoredChat(user_id=user.id, chat_id=cid, title=it.get("title", "") or "",
                                     username=uname, is_channel=bool(it.get("is_channel")), active=True))
            added += 1
            await asyncio.sleep(1.5)  # пауза между вступлениями — не выглядеть ботом
        db.commit()
    finally:
        await client.disconnect()
    return JSONResponse({"ok": True, "added": added})


# ── Telegram-бот (привязка лички для пушей/биллинга) ────────────────────
@app.get("/app/bot", response_class=HTMLResponse)
def bot_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    link = ""
    if not user.bot_linked:
        if not user.link_token:
            user.link_token = secrets.token_urlsafe(12)
            db.commit()
        link = platform_bot.deep_link(user.link_token)
    return render(request, "app/bot.html", user, active="bot", deep_link=link,
                  bot_username=platform_bot.USERNAME)


# ── billing ─────────────────────────────────────────────────────────────
@app.get("/app/billing", response_class=HTMLResponse)
def billing_page(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    return render(request, "app/billing.html", user, active="billing",
                  msg=request.query_params.get("msg"))

@app.post("/app/billing/checkout")
def billing_checkout(request: Request, plan: str = Form(...), db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    if plan not in billing.PLANS:
        return RedirectResponse("/app/billing?msg=Неизвестный тариф", status_code=303)
    link = billing.create_payment_link(plan, user)
    if link:
        return RedirectResponse(link, status_code=303)
    # manual-режим: активируем как тест и пишем в личку
    billing.activate(db, user, plan, provider="manual", ref="manual-activation")
    if user.alert_chat_id:
        platform_bot.send_html(user.alert_chat_id,
                               f"✅ Подписка <b>{billing.PLANS[plan]['title']}</b> активирована.")
    return RedirectResponse("/app/billing?msg=Тариф активирован (manual-режим)", status_code=303)

@app.post("/app/billing/cancel")
def billing_cancel(request: Request, db: Session = Depends(get_db)):
    user = get_user(request, db)
    if not user:
        return need_login()
    billing.cancel(db, user)
    return RedirectResponse("/app/billing?msg=Подписка отменена", status_code=303)

@app.post("/billing/webhook/{provider}")
async def billing_webhook(provider: str, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    ok = billing.handle_webhook(db, provider, payload)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 400)


@app.get("/healthz")
def healthz():
    return {"ok": True}
