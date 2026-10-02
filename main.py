import os
import asyncio
import re
import httpx
from fastapi import FastAPI, Request
import uvicorn
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Кот Ученый - Навык Алисы ChatBook")

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")
VOICE_SPEAKER = os.getenv("VOICE_SPEAKER", "ermil")  # Мужской голос: ermil, zahar

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
    """Форматирование реплики для синтезатора Яндекс Диалогов с мужским голосом."""
    # Убираем markdown форматирование
    clean = re.sub(r"[*_#`]", "", text)
    # Добавляем паузы после точек и восклицательных знаков для более живой речи
    clean = clean.replace(". ", ". silence<[300]> ")
    clean = clean.replace("! ", "! silence<[250]> ")
    clean = clean.replace("? ", "? silence<[300]> ")
    return f'<speaker voice="{speaker}">{clean}'

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
            return "Мяу... Я глубоко задумался над страницами книги. Повторите ваш вопрос, пожалуйста."
        except Exception:
            # Мягкий фоллбэк
            b_key = (book or "").strip().lower()
            if b_key in MOCK_QA_ANSWERS:
                return MOCK_QA_ANSWERS[b_key]
            return "Размышляя над этим фрагментом, важно помнить об историческом контексте и внутреннем конфликте героев."

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
    is_new_session = session.get("new", False)
    
    state = req.get("state", {})
    session_state = state.get("session", {})
    user_state = state.get("user", {})
    
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
            
    # 4. Меню оплаты и подписки
    if session_state.get("step") == "PAYMENT_MENU":
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
