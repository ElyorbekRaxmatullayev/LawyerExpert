# Техническая документация

Описание сайта — в [README.md](README.md). Здесь только код и запуск.

---

## Что это

Одностраничный сайт на Flask. Статика отдаётся как есть, форма заявки
шлёт POST на `/api/lead`, оттуда сообщение уходит в Telegram обычным
HTTP-запросом через `requests` — без telegram-библиотек.

```
app.py                     сервер: страница + приём заявок + отправка в Telegram
wsgi.py                    точка входа для gunicorn
passenger_wsgi.py          точка входа для Passenger (панели с «Setup Python App»)
requirements.txt           Flask, requests, gunicorn
.env.example               образец файла с секретами

templates/index.html       вся страница: разметка, стили и скрипты в одном файле
static/img/                логотипы, обложка для мессенджеров, фавиконка
static/robots.txt          индексация
static/sitemap.xml         карта сайта

deploy/nginx.conf          конфиг nginx для VPS
deploy/legalexpert.service unit systemd для автозапуска

php-fallback/              вариант без Python, для хостинга где есть только PHP
assets/logo-source/        исходные PNG логотипа в полном разрешении
```

---

## Локальный запуск

```bash
python -m venv venv
venv\Scripts\activate          # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Сайт: http://127.0.0.1:5000 · проверка живости: http://127.0.0.1:5000/health

---

## Настройки

Токен и получатели заданы прямо в `app.py` значениями по умолчанию.
Любое из них перекрывается переменной окружения или файлом `.env`
(скопируйте `.env.example` → `.env`).

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `BOT_TOKEN` | токен бота от @BotFather | зашит в `app.py` |
| `CHAT_IDS` | кому шлём заявки, id через запятую | `1913880636,5884034743,2077634702` |
| `SITE_URL` | адрес сайта в тексте заявки | `https://legalexpert.uz` |
| `RATE_LIMIT` | заявок с одного IP за окно | `3` |
| `RATE_WINDOW` | длина окна, секунд | `600` |
| `PORT` | порт локального запуска | `5000` |

**Бот:** `@legalsultan_bot` (id 8984770867).

> ⚠️ Каждый получатель обязан **сам написать боту `/start`** — Telegram не
> даёт ботам писать первыми. Если человек этого не сделал, его заявки
> не дойдут, а в журнале будет `chat not found`.

### Как узнать свой chat_id

Написать боту любое сообщение и открыть в браузере:

```
https://api.telegram.org/bot<ТОКЕН>/getUpdates
```

Нужное значение — `result[].message.chat.id`.

### Проверка доставки

```bash
curl -X POST https://legalexpert.uz/api/lead ^
  -H "Content-Type: application/json" ^
  -d "{\"name\":\"Проверка\",\"tel\":\"+998900000000\",\"type\":\"Договор\",\"msg\":\"тест\"}"
```

Ответ `{"ok":true}` — сообщение ушло во все чаты, где боту нажали `/start`.

---

## Развёртывание на Hostinger

Hostinger запускает Python **только на VPS** — на shared и cloud тарифах
нет root-доступа и Python не поднимается. Поэтому два пути.

### Вариант A — VPS (основной, с Flask)

```bash
# 1. код
apt update && apt install -y python3-venv nginx git
git clone <репозиторий> /var/www/legalexpert
cd /var/www/legalexpert
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 2. секреты отдельным файлом, не в git
cp .env.example .env && nano .env
chmod 600 .env && chown www-data:www-data .env

# 3. автозапуск
cp deploy/legalexpert.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now legalexpert

# 4. nginx
cp deploy/nginx.conf /etc/nginx/sites-available/legalexpert
ln -s /etc/nginx/sites-available/legalexpert /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 5. https
apt install -y certbot python3-certbot-nginx
certbot --nginx -d legalexpert.uz -d www.legalexpert.uz
```

Владелец файлов: `chown -R www-data:www-data /var/www/legalexpert`
(иначе не запишется `leads.log`).

Журнал: `journalctl -u legalexpert -f`
Перезапуск после обновления кода: `systemctl restart legalexpert`

### Вариант B — обычный хостинг, без Python

Если тариф без VPS, работает та же страница, только форму обрабатывает PHP.
Логика и текст сообщения идентичны Flask-версии.

В `public_html` кладём:

```
public_html/
├── index.html          <- templates/index.html
├── .htaccess           <- php-fallback/.htaccess
├── static/             <- вся папка static
└── api/
    └── lead.php        <- php-fallback/api/lead.php
```

`.htaccess` делает `/api/lead` псевдонимом `/api/lead.php`, включает https,
режет `www` и закрывает `leads.log` от посторонних. Фронтенд не меняется:
он и так шлёт запрос на относительный `api/lead`.

### Домен

`legalexpert.uz` направляется на сервер A-записью в панели регистратора
или в hPanel → Домены → DNS.

---

## Приём заявок: как устроено

1. Скрипт на странице собирает поля и шлёт `POST api/lead` в JSON.
2. Сервер отсекает мусор:
   - **honeypot** — скрытое поле `company`; если оно заполнено, значит
     это бот. Отвечаем `ok`, но никуда не шлём;
   - **лимит по IP** — не больше `RATE_LIMIT` заявок за `RATE_WINDOW`;
   - **очистка** — html-теги вырезаются, длина полей ограничена.
3. Сообщение уходит во все `CHAT_IDS`. Успех — если дошло хотя бы в один.
4. Копия любой заявки пишется в `leads.log` рядом с `app.py` — страховка
   на случай, если Telegram недоступен. Файл в `.gitignore`.

Ответы `/api/lead`:

| Код | Значение | Что видит посетитель |
|---|---|---|
| 200 | принято | «Заявка отправлена» |
| 400 | нет имени или контакта | подсветка пустого поля |
| 429 | сработал лимит | предложение написать в WhatsApp |
| 502 | Telegram недоступен | ссылки на WhatsApp и телефон |

Если запрос вообще не ушёл, форма показывает прямые ссылки на WhatsApp и
звонок — заявка не теряется.

---

## Правка контента

| Что | Где |
|---|---|
| Телефон, почта, WhatsApp, Telegram, город | блок `CONFIG` в конце `templates/index.html` |
| Заголовок и описание для поиска | `<title>` и `<meta name="description">` |
| Цифры «более 7 лет», «300+ экспертиз» | поиск по слову `ЦИФРЫ` |
| Отзывы | секция `id="reviews"` |
| Токен и получатели заявок | `app.py` или `.env` |

Все цвета и шрифты — токены в `:root` в начале `<style>`.

Логотип используется в трёх местах: `mark-color.png` в шапке,
`logo-white.png` на тёмной панели первого экрана, `mark-white.png` в подвале.
Веб-версии собираются из `assets/logo-source/` — при замене логотипа
пересоберите их в том же размере и с теми же именами.

---

## Безопасность

**Токен бота лежит в репозитории** — так было решено на время запуска.
Держать его в публичном репозитории нельзя: боты-сканеры находят такие
токены в GitHub за считанные минуты, и чужой человек сможет писать от
имени бота и читать заявки.

Когда будете убирать:

1. `@BotFather` → `/revoke` → выбрать бота → получить новый токен
2. Новый токен положить в `.env` на сервере (не в git)
3. В `app.py` заменить значение по умолчанию на пустую строку:
   `BOT_TOKEN = os.environ.get("BOT_TOKEN", "")`
4. Сделать репозиторий приватным либо переписать историю
   (`git filter-repo`) — старый коммит с токеном остаётся в истории

Уже сделано: `.env` и `leads.log` в `.gitignore`, заголовки `X-Frame-Options`,
`X-Content-Type-Options`, `Referrer-Policy`, экранирование пользовательского
ввода перед отправкой в Telegram, лимит запросов по IP.

---

## Что стоит добавить позже

- Реальное фото юриста на первом экране — по подборкам сайтов
  частнопрактикующих юристов это самый сильный элемент доверия
- 2–3 кейса: задача → что нашлось → результат, без имён клиентов
- Сканы диплома и сертификатов
- Страница политики конфиденциальности (форма её подразумевает)
- Узбекская версия сайта
- Яндекс.Метрика или Google Analytics и цель на отправку заявки
