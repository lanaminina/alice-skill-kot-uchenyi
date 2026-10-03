import os
import asyncio
import re
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Кот Ученый - Навык Алисы ChatBook")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")
VOICE_SPEAKER = os.getenv("VOICE_SPEAKER", "erkanyavas")  # Голос навыка (выбран в Диалогах: erkanyavas - Эркан Явас)

BOOKS = ["Гранатовый браслет", "Вишневый сад", "Гроза"]
FREE_LIMIT = int(os.getenv("FREE_LIMIT", "5"))

# Системные промпты на основе SKILL.md "Кота Ученого"
SYSTEM_PROMPT_BASE = (
    "Ты — Кот Ученый, лингвист-филолог 45-50 лет. У тебя глубокая, интеллигентная гуманитарная речь, "
    "без жаргона. Ты мыслишь философски, доброжелателен, беспристрастен, не оцениваешь автора и читателя. "
    "Отвечай емко (2-3 предложения, до 350 символов), так как твой ответ будет озвучен вслух в умном голосовом помощнике Алисе."
)

PROMPT_QA = SYSTEM_PROMPT_BASE + " Твоя задача: дать точный, глубокий ответ на вопрос читателя по произведению «{book}»."

PROMPT_SOCRATIC = SYSTEM_PROMPT_BASE + (
    " Мы ведем Сократический диалог по произведению «{book}». Шаг: {step}. "
    "НЕ ДАВАЙ ГОТОВЫХ ОТВЕТОВ. Задай только ОДИН наводящий вопрос по таксономии Блума, чтобы подтолкнуть собеседника "
    "к самостоятельному осмыслению мотивов и конфликта. Опирайся на последнюю реплику."
)

MOCK_QA_ANSWERS = {
    "гранатовый браслет": (
        "В повести Куприна браслет с редким зеленым гранатом символизирует бескорыстную, благоговейную любовь, "
        "граничащую с самопожертвованием. Это редкий дар, который случается раз в сотни лет, но княгиня Вера осознает его ценность лишь после гибели Желткова."
    ),
    "вишневый сад": (
        "Вишневый сад у Чехова — это многогранный символ уходящей дворянской эпохи, беззаботной юности и вечной красоты. "
        "Для Раневской сад — воспоминание о прошлом, для Лопахина — коммерческий ресурс, а для Пети Трофимова — вся Россия, требующая обновления."
    ),
    "гроза": (
        "Трагедия Катерины в пьесе Островского — это столкновение живой, искренней и свободной души с душным, лицемерным миром купеческого самодурства. "
        "Ее финал — не слабость, а единственный доступный ей способ остаться верной себе и не подчиниться «темному царству»."
    )
}

def normalize_book(query: str):
    q = (query or "").lower().replace("ё", "е")
    if "вишн" in q or "сад" in q:
        return "Вишнёвый сад"
    if "гранат" in q or "браслет" in q:
        return "Гранатовый браслет"
    if "гроз" in q:
        return "Гроза"
    return None

def format_tts(text: str, speaker: str = VOICE_SPEAKER) -> str:
    """Форматирование реплики для синтезатора Яндекс Диалогов с голосом навыка."""
    # Убираем markdown форматирование
    clean = re.sub(r"[*_#`]", "", text)
    # Добавляем паузы после точек и восклицательных знаков для более живой речи
    clean = clean.replace(". ", ". silence<[300]> ")
    clean = clean.replace("! ", "! silence<[250]> ")
    clean = clean.replace("? ", "? silence<[300]> ")
    if speaker and speaker.lower() not in ["", "none", "default"]:
        return f'<speaker voice="{speaker}">{clean}'
    return clean

async def ask_yandexgpt(system_text: str, user_text: str, book: str = "", max_tokens: int = 150) -> str:
    """Асинхронный вызов YandexGPT API с быстрым fallback при отсутствии ключей."""
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        # Режим демонстрации / мок-ответов
        b_key = (book or "").strip().lower()
        if b_key in MOCK_QA_ANSWERS:
            return MOCK_QA_ANSWERS[b_key]
        return "В этом произведении каждый образ раскрывает вечные темы человеческой души, долга и искреннего чувства."
        
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "x-folder-id": YANDEX_FOLDER_ID
    }
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.4,
            "maxTokens": str(max_tokens)
        },
        "messages": [
            {"role": "system", "text": system_text},
            {"role": "user", "text": user_text}
        ]
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # Таймаут 3.5 сек для укладки в лимит 4.5 сек платформы Алисы
            response = await client.post(url, headers=headers, json=payload, timeout=3.5)
            response.raise_for_status()
            result = response.json()
            return result["result"]["alternatives"][0]["message"]["text"].strip()
        except asyncio.TimeoutError:
            return "Мяу... Я глубоко задумался над страницами книги. Минуточку..."
        except Exception:
            # Мягкий фоллбэк
            b_key = (book or "").strip().lower()
            if b_key in MOCK_QA_ANSWERS:
                return MOCK_QA_ANSWERS[b_key]
            return "Размышляя над этим фрагментом, важно помнить об историческом контексте и внутреннем конфликте героев."

TEST_UI_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Кот Учёный — Тестер навыка Алисы (ChatBook)</title>
<link rel="icon" type="image/png" href="/static/cat-avatar.png">
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #ececf1; margin: 0; padding: 20px; display: flex; justify-content: center; min-height: 94vh; box-sizing: border-box; }
  .phone { width: 100%; max-width: 480px; background: #fff; border-radius: 28px; box-shadow: 0 16px 40px rgba(0,0,0,0.12); overflow: hidden; display: flex; flex-direction: column; height: 90vh; }
  .header { background: linear-gradient(135deg, #4a3df5, #673ab7); color: #fff; padding: 14px 18px; font-size: 17px; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
  .header-left { display: flex; align-items: center; gap: 12px; }
  .avatar-header { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; border: 2px solid #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }
  .header-title { display: flex; flex-direction: column; }
  .header-name { font-size: 16px; font-weight: 600; }
  .header-sub { font-size: 12px; opacity: 0.85; }
  .voice-tag { font-size: 11px; background: rgba(255,255,255,0.2); padding: 4px 8px; border-radius: 12px; font-weight: 500; }
  .chat { flex: 1; padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; background: #fdfdfd; }
  .msg-row { display: flex; gap: 10px; align-items: flex-end; width: 100%; }
  .msg-row.bot { justify-content: flex-start; }
  .msg-row.user { justify-content: flex-end; }
  .msg-avatar { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; flex-shrink: 0; box-shadow: 0 2px 6px rgba(0,0,0,0.1); }
  .msg { max-width: 80%; padding: 12px 16px; border-radius: 20px; font-size: 15px; line-height: 1.45; word-break: break-word; }
  .bot .msg { background: #f0efff; color: #231d5e; border-bottom-left-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
  .user .msg { background: linear-gradient(135deg, #4a3df5, #5e35b1); color: #fff; border-bottom-right-radius: 4px; }
  .card-banner { width: 100%; border-radius: 14px; margin-bottom: 10px; display: block; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
  .tts-badge { font-size: 11px; color: #726e9c; margin-top: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: rgba(74,61,245,0.06); padding: 6px 8px; border-radius: 8px; border-left: 3px solid #4a3df5; }
  .buttons-bar { padding: 10px 16px; display: flex; gap: 8px; flex-wrap: wrap; background: #fafafa; border-top: 1px solid #eee; max-height: 120px; overflow-y: auto; }
  .btn-pill { background: #fff; border: 1.5px solid #4a3df5; color: #4a3df5; border-radius: 20px; padding: 7px 15px; font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.15s; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
  .btn-pill:hover { background: #4a3df5; color: #fff; transform: translateY(-1px); }
  .input-bar { padding: 12px 16px; display: flex; gap: 10px; background: #fff; border-top: 1px solid #eee; }
  .input-bar input { flex: 1; padding: 11px 16px; border: 1.5px solid #e0e0e0; border-radius: 22px; font-size: 15px; outline: none; transition: border-color 0.2s; }
  .input-bar input:focus { border-color: #4a3df5; }
  .input-bar button { background: linear-gradient(135deg, #4a3df5, #673ab7); color: #fff; border: none; border-radius: 22px; padding: 10px 20px; font-weight: 600; cursor: pointer; font-size: 14px; }
</style>
</head>
<body>
<div class="phone">
  <div class="header">
    <div class="header-left">
      <img src="/static/cat-avatar.png" alt="Кот Учёный" class="avatar-header">
      <div class="header-title">
        <span class="header-name">Кот Учёный</span>
        <span class="header-sub">ChatBook • Навык Алисы</span>
      </div>
    </div>
    <span class="voice-tag">Голос: Эркан Явас ♂</span>
  </div>
  <div class="chat" id="chat"></div>
  <div class="buttons-bar" id="buttons"></div>
  <form class="input-bar" id="form" onsubmit="sendText(event)">
    <input type="text" id="userInput" placeholder="Спросите Кота Учёного о книге..." autocomplete="off">
    <button type="submit">Отправить</button>
  </form>
</div>
<script>
let sessionState = {};
let userState = {};
let isNew = true;
let isFirstMsg = true;

async function sendRequest(text) {
  const chat = document.getElementById("chat");
  if (text) {
    const userRow = document.createElement("div");
    userRow.className = "msg-row user";
    userRow.innerHTML = `<div class="msg">${escapeHtml(text)}</div>`;
    chat.appendChild(userRow);
    chat.scrollTop = chat.scrollHeight;
  }
  
  const payload = {
    request: { original_utterance: text, command: text ? text.toLowerCase() : "" },
    session: { new: isNew, session_id: "web_test_session", user_id: "web_test_user" },
    state: { session: sessionState, user: userState },
    version: "1.0"
  };
  isNew = false;
  
  try {
    const res = await fetch("/webhook", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    const resp = data.response || {};
    
    sessionState = data.session_state || {};
    userState = data.user_state_update || {};
    
    const botRow = document.createElement("div");
    botRow.className = "msg-row bot";
    
    let innerContent = "";
    if (isFirstMsg) {
      innerContent += `<img src="/static/cat-card.png" class="card-banner" alt="Кот Учёный на страницах книги">`;
      isFirstMsg = false;
    }
    innerContent += `<div>${escapeHtml(resp.text || "")}</div>`;
    if (resp.tts) {
      innerContent += `<div class="tts-badge"><b>🔊 TTS:</b> ${escapeHtml(resp.tts)}</div>`;
    }
    
    botRow.innerHTML = `
      <img src="/static/cat-avatar.png" class="msg-avatar" alt="Кот">
      <div class="msg">${innerContent}</div>
    `;
    chat.appendChild(botRow);
    chat.scrollTop = chat.scrollHeight;
    
    // Рендер кнопок
    const btnContainer = document.getElementById("buttons");
    btnContainer.innerHTML = "";
    const buttons = resp.buttons || [];
    buttons.forEach(b => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-pill";
      btn.innerText = b.title;
      btn.onclick = () => sendRequest(b.title);
      btnContainer.appendChild(btn);
    });
  } catch (err) {
    console.error(err);
  }
}

function sendText(e) {
  e.preventDefault();
  const input = document.getElementById("userInput");
  const val = input.value.trim();
  if (val) {
    input.value = "";
    sendRequest(val);
  }
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

window.onload = () => sendRequest("");
</script>
</body>
</html>"""

# Локальное серверное хранилище сессий (надежный fallback, если клиент/Яндекс не передает state)
SERVER_SESSIONS: dict = {}

@app.get("/", response_class=HTMLResponse)
@app.get("/test", response_class=HTMLResponse)
async def test_page():
    return HTMLResponse(content=TEST_UI_HTML)

@app.post("/webhook")
async def alice_webhook(request: Request):
    req = await request.json()
    
    # 1. Healthcheck платформы
    original_utterance = req.get("request", {}).get("original_utterance", "").strip()
    raw_command = req.get("request", {}).get("command", "").strip()
    command_text = raw_command if raw_command else original_utterance
    command_lower = command_text.lower()
    
    if original_utterance.lower() == "ping":
        return {"response": {"text": "pong", "end_session": False}, "version": "1.0"}
        
    session = req.get("session", {})
    session_id = session.get("session_id", "default_session")
    is_new_session = session.get("new", False)
    
    state = req.get("state", {})
    # Если клиент не прислал state.session, берем из серверного кэша по session_id
    raw_session_state = state.get("session", {})
    if is_new_session:
        session_state = {}
        SERVER_SESSIONS.pop(session_id, None)
    else:
        session_state = raw_session_state if raw_session_state else SERVER_SESSIONS.get(session_id, {})
    
    user_state = state.get("user", {})
    
    print(f"DEBUG REQ: new={is_new_session}, sid='{session_id[:8]}', cmd='{command_lower}', state={session_state}")
    
    new_session_state = session_state.copy()
    new_user_state = user_state.copy()
    
    current_requests = new_user_state.get("daily_requests", 0)
    has_subscription = new_user_state.get("has_subscription", False)
    
    text = ""
    tts_custom = None
    buttons = []
    end_session = False
    
    # 2. Обязательные интенты Яндекс Диалогов: Помощь и Что ты умеешь
    intents = req.get("request", {}).get("nlu", {}).get("intents", {})
    is_help = "YANDEX.HELP" in intents or "помощь" in command_lower or "что ты умеешь" in command_lower
    
    if is_help:
        text = (
            "Я Кот Учёный — ваш проводник и собеседник по классической литературе. "
            "Я помогаю глубже понять мотивы героев, символы и скрытые смыслы произведений. "
            "Вы можете задавать любые вопросы по книге или выбрать Сократический диалог, "
            "где я помогу вам самому дойти до сути через размышления. "
            "Какую книгу откроем: «Гранатовый браслет», «Вишнёвый сад» или «Грозу»?"
        )
        buttons = [{"title": b, "hide": True} for b in BOOKS]
        new_session_state["step"] = "CHOOSE_BOOK"
        return {
            "response": {
                "text": text,
                "tts": format_tts(text),
                "buttons": buttons,
                "end_session": False
            },
            "session_state": new_session_state,
            "user_state_update": new_user_state,
            "version": "1.0"
        }
        
    # 3. Проверка лимитов перед обработкой запросов к модели
    if session_state.get("step") in ["QA_LOOP", "SOCRATIC_LOOP"] and not is_new_session and not has_subscription:
        if current_requests >= FREE_LIMIT:
            text = (
                "Мяу... Наша ознакомительная беседа на сегодня подошла к концу. "
                "Чтобы продолжить литературные исследования без ограничений, "
                "вы можете оформить подписку «Чатбук Плюс»."
            )
            buttons = [
                {"title": "Оформить подписку", "hide": True},
                {"title": "Выбрать другую книгу", "hide": True},
                {"title": "Хватит на сегодня", "hide": True}
            ]
            new_session_state["step"] = "PAYMENT_MENU"
            return {
                "response": {
                    "text": text,
                    "tts": format_tts(text),
                    "buttons": buttons,
                    "end_session": False
                },
                "session_state": new_session_state,
                "user_state_update": new_user_state,
                "version": "1.0"
            }
            
    # Быстрый перехват названия книги (если пользователь в любой момент называет книгу)
    book_override = normalize_book(command_lower)
    if book_override and session_state.get("step") != "QA_LOOP" and session_state.get("step") != "SOCRATIC_LOOP":
        new_session_state["book"] = book_override
        new_session_state["step"] = "CHOOSE_MODE"
        text = (
            f"Прекрасно, открываем «{book_override}». "
            "Мы можем разобрать любые ваши вопросы напрямую (режим «Вопрос-ответ») "
            "или провести Сократический диалог, где я буду задавать наводящие вопросы к сути. "
            "Что вам ближе?"
        )
        buttons = [
            {"title": "Вопрос-ответ", "hide": True},
            {"title": "Сократический диалог", "hide": True}
        ]
    # 4. Меню оплаты и подписки
    elif session_state.get("step") == "PAYMENT_MENU":
        if "подписк" in command_lower or "оформить" in command_lower or "купить" in command_lower:
            text = (
                "Подписка «Чатбук Плюс» открывает безлимитный доступ ко всем книгам школьной и классической программы, "
                "детальным разборам и сократическим тренировкам. Функция нативной оплаты в Алисе сейчас на этапе подключения. "
                "В тестовом режиме мы дарим вам еще 5 вопросов!"
            )
            # В тестовом режиме добавляем еще попыток
            new_user_state["daily_requests"] = max(0, current_requests - 5)
            buttons = [{"title": "Продолжить разбор", "hide": True}]
            new_session_state["step"] = "CHOOSE_BOOK"
        elif "хватит" in command_lower or "пока" in command_lower or "выход" in command_lower:
            text = "Благодарю за содержательную беседу. До новых встреч на страницах книг!"
            end_session = True
        else:
            text = "Давайте вернемся к литературе. Какое произведение откроем?"
            new_session_state["step"] = "CHOOSE_BOOK"
            buttons = [{"title": b, "hide": True} for b in BOOKS]

    # 5. Новая сессия (старт)
    elif is_new_session:
        instant_book = normalize_book(command_lower)
        if instant_book:
            new_session_state["book"] = instant_book
            new_session_state["step"] = "CHOOSE_MODE"
            text = (
                f"Здравствуйте! Я Кот Учёный. Открываем «{instant_book}». "
                "Мы можем разобрать любые ваши вопросы напрямую (режим «Вопрос-ответ») "
                "или провести Сократический диалог, где я буду задавать наводящие вопросы к сути. "
                "Что вам ближе?"
            )
            buttons = [
                {"title": "Вопрос-ответ", "hide": True},
                {"title": "Сократический диалог", "hide": True}
            ]
        else:
            text = (
                "Здравствуйте! Я Кот Учёный, филолог и ваш проводник по смыслам классической литературы. "
                "Сегодня мы можем исследовать «Гранатовый браслет», «Вишнёвый сад» или «Грозу». "
                "Какое произведение выберем для беседы?"
            )
            buttons = [{"title": b, "hide": True} for b in BOOKS]
            new_session_state["step"] = "CHOOSE_BOOK"
        
    elif session_state.get("step") == "CHOOSE_BOOK":
        chosen_book = normalize_book(command_lower)
        if chosen_book:
            new_session_state["book"] = chosen_book
            new_session_state["step"] = "CHOOSE_MODE"
            text = (
                f"Прекрасно, открываем «{chosen_book}». "
                "Мы можем разобрать любые ваши вопросы напрямую (режим «Вопрос-ответ») "
                "или провести Сократический диалог, где я буду задавать наводящие вопросы к сути. "
                "Что вам ближе?"
            )
            buttons = [
                {"title": "Вопрос-ответ", "hide": True},
                {"title": "Сократический диалог", "hide": True}
            ]
        else:
            text = "Пожалуйста, назовите одну из книг: «Гранатовый браслет», «Вишнёвый сад» или «Гроза»."
            buttons = [{"title": b, "hide": True} for b in BOOKS]
            
    elif session_state.get("step") == "CHOOSE_MODE":
        if "вопрос" in command_lower or "ответ" in command_lower:
            new_session_state["mode"] = "qa"
            new_session_state["step"] = "QA_LOOP"
            text = f"Слушаю вас внимательно. Какой вопрос по «{session_state.get('book')}» вас волнует?"
        elif "сократ" in command_lower or "диалог" in command_lower or "разбор" in command_lower:
            new_session_state["mode"] = "socratic"
            new_session_state["step"] = "SOCRATIC_LOOP"
            new_session_state["socratic_step"] = 1
            text = f"Начнем наше размышление над «{session_state.get('book')}». Как вы думаете, в чем здесь центральный конфликт героев?"
        else:
            text = "Выберите режим беседы: скажите «Вопрос-ответ» или «Сократический диалог»."
            buttons = [
                {"title": "Вопрос-ответ", "hide": True},
                {"title": "Сократический диалог", "hide": True}
            ]
            
    elif session_state.get("step") == "QA_LOOP":
        book = session_state.get("book", "")
        sys_prompt = PROMPT_QA.format(book=book)
        
        answer = await ask_yandexgpt(sys_prompt, original_utterance, book=book)
        text = answer
        new_user_state["daily_requests"] = current_requests + 1
        buttons = [
            {"title": "Задать еще вопрос", "hide": True},
            {"title": "Сменить книгу", "hide": True}
        ]
        if "сменить" in command_lower or "другая книга" in command_lower:
            text = "Какую книгу откроем теперь: «Гранатовый браслет», «Вишнёвый сад» или «Гроза»?"
            new_session_state["step"] = "CHOOSE_BOOK"
            buttons = [{"title": b, "hide": True} for b in BOOKS]
            
    elif session_state.get("step") == "SOCRATIC_LOOP":
        book = session_state.get("book", "")
        current_step = session_state.get("socratic_step", 1)
        sys_prompt = PROMPT_SOCRATIC.format(book=book, step=current_step)
        
        answer = await ask_yandexgpt(sys_prompt, original_utterance, book=book)
        text = answer
        new_user_state["daily_requests"] = current_requests + 1
        new_session_state["socratic_step"] = current_step + 1
        
        if new_session_state["socratic_step"] > 3:
            text += "\n\nМы завершили базовый цикл размышления над произведением. Вы продемонстрировали зрелый взгляд на мотивы героев!"
            buttons = [{"title": "Разобрать другую книгу", "hide": True}]
            new_session_state["step"] = "CHOOSE_BOOK"
            
    else:
        text = "Давайте вернемся к началу. Какую книгу будем исследовать?"
        new_session_state = {"step": "CHOOSE_BOOK"}
        buttons = [{"title": b, "hide": True} for b in BOOKS]

    # Обрезка под жесткий лимит 1024 символов платформы Яндекс Диалогов
    if len(text) > 1024:
        text = text[:1020] + "..."

    final_tts = tts_custom or format_tts(text)

    # Сохраняем в локальный серверный кэш
    SERVER_SESSIONS[session_id] = new_session_state

    return {
        "response": {
            "text": text,
            "tts": final_tts,
            "buttons": buttons,
            "end_session": end_session
        },
        "session_state": new_session_state,
        "user_state_update": new_user_state,
        "version": "1.0"
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
