import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix


def _load_env_file(name=".env"):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file()


BOT_TOKEN = os.environ.get(
    "BOT_TOKEN",
    "8984770867:AAEsncu5pSp9gWpwLwb-grGGtfskIXVgKhw",
)

CHAT_IDS = [
    c.strip()
    for c in os.environ.get("CHAT_IDS", "1913880636,5884034743,2077634702").split(",")
    if c.strip()
]

SITE_NAME = "Bekmuratov Legal Expertise"
SITE_URL = os.environ.get("SITE_URL", "https://legalexpert.uz")

RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "3"))
RATE_WINDOW = int(os.environ.get("RATE_WINDOW", "600"))

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
REQUEST_TIMEOUT = 8

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEADS_LOG = os.path.join(BASE_DIR, "leads.log")


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
)
log = logging.getLogger("site")

_hits = {}
_hits_lock = threading.Lock()

MAX_LEN = {"name": 100, "tel": 100, "type": 80, "msg": 2000}
TAG_RE = re.compile(r"<[^>]*>")


def client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "-")


def rate_limited(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        q = _hits.setdefault(ip, deque())
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return True
        q.append(now)
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW]:
                _hits.pop(k, None)
    return False


def clean(value, field: str) -> str:
    text = TAG_RE.sub("", str(value or "")).strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text[: MAX_LEN.get(field, 200)]


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(lead: dict) -> str:
    when = datetime.now().strftime("%d.%m.%Y %H:%M")
    rows = [
        "🔔 <b>Новая заявка с сайта</b>",
        "",
        f"👤 <b>Имя:</b> {escape_html(lead['name'])}",
        f"📞 <b>Контакт:</b> {escape_html(lead['tel'])}",
        f"📄 <b>Документ:</b> {escape_html(lead['type'] or '—')}",
        "",
        f"📝 <b>Задача:</b>\n{escape_html(lead['msg'] or '—')}",
        "",
        f"🕒 {when}  ·  {escape_html(SITE_URL)}",
    ]
    return "\n".join(rows)


def send_to_telegram(text: str) -> int:
    if not BOT_TOKEN or not CHAT_IDS:
        log.error("не задан BOT_TOKEN или CHAT_IDS — заявка не отправлена")
        return 0

    url = TELEGRAM_API.format(token=BOT_TOKEN, method="sendMessage")
    delivered = 0

    for chat_id in CHAT_IDS:
        try:
            r = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=REQUEST_TIMEOUT,
            )
            if r.ok and r.json().get("ok"):
                delivered += 1
            else:
                log.warning("telegram %s: %s %s", chat_id, r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning("telegram %s: %s", chat_id, e)

    return delivered


def save_backup(lead: dict, delivered: int) -> None:
    try:
        with open(LEADS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"ts": datetime.now().isoformat(timespec="seconds"),
                 "delivered": delivered, **lead},
                ensure_ascii=False,
            ) + "\n")
    except OSError as e:
        log.warning("не удалось записать %s: %s", LEADS_LOG, e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/lead", methods=["POST"])
def api_lead():
    data = request.get_json(silent=True) or request.form.to_dict() or {}

    if clean(data.get("hp"), "name"):
        log.info("honeypot сработал, ip=%s", client_ip())
        return jsonify(ok=True)

    lead = {
        "name": clean(data.get("name"), "name"),
        "tel": clean(data.get("tel"), "tel"),
        "type": clean(data.get("type"), "type"),
        "msg": clean(data.get("msg"), "msg"),
    }

    if not lead["name"] or not lead["tel"]:
        return jsonify(ok=False, error="Заполните имя и контакт"), 400

    ip = client_ip()
    if rate_limited(ip):
        log.info("лимит заявок, ip=%s", ip)
        return jsonify(ok=False, error="Заявка уже отправлена"), 429

    delivered = send_to_telegram(build_message(lead))
    save_backup(lead, delivered)

    if delivered:
        log.info("заявка от %r доставлена в %d чат(ов)", lead["name"], delivered)
        return jsonify(ok=True)

    log.error("заявка от %r НЕ доставлена ни в один чат", lead["name"])
    return jsonify(ok=False, error="Сервис уведомлений недоступен"), 502


@app.route("/health")
def health():
    return jsonify(ok=True, service=SITE_NAME, recipients=len(CHAT_IDS))


@app.route("/robots.txt")
@app.route("/sitemap.xml")
def seo_files():
    return send_from_directory(app.static_folder, request.path.lstrip("/"))


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico")


@app.errorhandler(404)
def not_found(_):
    return render_template("index.html"), 404


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    log.info("%s → http://127.0.0.1:%d  (получателей заявок: %d)",
             SITE_NAME, port, len(CHAT_IDS))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("DEBUG")))
