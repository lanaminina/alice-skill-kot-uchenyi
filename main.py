import os
import asyncio
import httpx
from fastapi import FastAPI, Request
import uvicorn
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Кот Ученый - Навык Алисы ChatBook")

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")

BOOKS = ["Гранатовый браслет", "Вишневый сад", "Гроза"]
FREE_LIMIT = 5  # Лимит бесплатных запросов к LLM

# Системные промпты на основе SKILL.md "Кота Ученого"
SYSTEM_PROMPT_BASE = (
    "Ты — Кот Ученый, лингвист-филолог 45-50 лет. У тебя глубокая, гуманитарная речь, "
    "без жаргона. Ты беспристрастен, не оцениваешь автора, мыслишь философски. "
    "Отвечай крайне кратко (не более 2-3 предложений, до 300 символов), так как твой ответ "
    "будет озвучен голосовым помощником Алисой. "
)

PROMPT_QA = SYSTEM_PROMPT_BASE + "Твоя задача: дать точный, глубокий ответ на вопрос пользователя по произведению «{book}»."

PROMPT_SOCRATIC = SYSTEM_PROMPT_BASE + (
    "Мы находимся в режиме Сократического диалога по произведению «{book}». Шаг: {step}. "
    "НЕ ДАВАЙ ГОТОВЫХ ОТВЕТОВ. Задай только ОДИН наводящий вопрос, чтобы подтолкнуть ученика "
    "к самостоятельному выводу (по таксономии Блума). Опирайся на последнюю реплику ученика."
)

async def ask_yandexgpt(system_text: str, user_text: str, max_tokens: int = 150) -> str:
    """Прямой асинхронный вызов YandexGPT API для минимальной задержки."""
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return "[API ключи не настроены. Здесь был бы ответ нейросети]"
        
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
            # Жесткий таймаут 3.5 сек, чтобы спасти сессию Алисы
            response = await client.post(url, headers=headers, json=payload, timeout=3.5)
            response.raise_for_status()
            result = response.json()
            return result["result"]["alternatives"][0]["message"]["text"]
        except asyncio.TimeoutError:
            return "Мяу... Я глубоко задумался. Задайте этот вопрос ещё раз, пожалуйста."
        except Exception as e:
            return "Произошла ошибка при доступе к моим фолиантам. Давайте попробуем снова."

@app.post("/webhook")
async def alice_webhook(request: Request):
    req = await request.json()
    
    original_utterance = req.get("request", {}).get("original_utterance", "").strip().lower()
    if original_utterance == "ping":
        return {"response": {"text": "pong", "end_session": False}, "version": "1.0"}
        
    session = req.get("session", {})
    is_new_session = session.get("new", False)
    
    state = req.get("state", {})
    session_state = state.get("session", {})
    user_state = state.get("user", {})
    
    new_session_state = session_state.copy()
    new_user_state = user_state.copy()
    
    # Инициализация счетчика запросов для пользователя, если его нет
    current_requests = new_user_state.get("daily_requests", 0)
    
    text = ""
    tts = ""
    buttons = []
    end_session = False
    
    # 0. Проверка лимитов ПЕРЕД обработкой запроса к LLM
    # Проверяем лимит только если пользователь уже в цикле общения с LLM
    if session_state.get("step") in ["QA_LOOP", "SOCRATIC_LOOP"] and not is_new_session:
        if current_requests >= FREE_LIMIT:
            text = (
                "Мяу... Наши бесплатные диалоги на сегодня исчерпаны. Я с удовольствием "
                "продолжу наши литературные изыскания, как только вы оформите подписку "
                "«Чатбук Плюс» в приложении. До скорой встречи!"
            )
            # Формируем ответ с paywall и завершаем сессию
            return {
                "response": {
                    "text": text,
                    "end_session": True
                },
                "session_state": new_session_state,
                "user_state_update": new_user_state,
                "version": "1.0"
            }
    
    if is_new_session:
        text = ("Мяу! Я Кот Учёный, ваш проводник по смыслам классической литературы. "
                "Сегодня в каталоге: «Гранатовый браслет», «Вишнёвый сад» и «Гроза». "
                "Какое произведение будем разбирать?")
        tts = text
        buttons = [{"title": b, "hide": True} for b in BOOKS]
        new_session_state["step"] = "CHOOSE_BOOK"
        
    elif session_state.get("step") == "CHOOSE_BOOK":
        chosen_book = next((b for b in BOOKS if b.lower() in original_utterance), None)
        if chosen_book:
            new_session_state["book"] = chosen_book
            new_session_state["step"] = "CHOOSE_MODE"
            text = (f"Открываем «{chosen_book}». Мы можем обсудить ваши вопросы напрямую "
                    "(режим вопрос-ответ), или я проведу вас через сократический диалог. "
                    "Что выбираете?")
            buttons = [{"title": "Вопрос-ответ", "hide": True}, {"title": "Сократический диалог", "hide": True}]
        else:
            text = "Выберите, пожалуйста, из трёх: «Гранатовый браслет», «Вишнёвый сад» или «Гроза»."
            buttons = [{"title": b, "hide": True} for b in BOOKS]
            
    elif session_state.get("step") == "CHOOSE_MODE":
        if "вопрос" in original_utterance or "ответ" in original_utterance:
            new_session_state["mode"] = "qa"
            new_session_state["step"] = "QA_LOOP"
            text = "Прекрасно. Задавайте ваш вопрос, и мы вместе докопаемся до сути."
        elif "сократ" in original_utterance or "разбор" in original_utterance or "диалог" in original_utterance:
            new_session_state["mode"] = "socratic"
            new_session_state["step"] = "SOCRATIC_LOOP"
            new_session_state["socratic_step"] = 1
            text = "Отличный выбор. Начнем с простого: в чем, по-вашему, главная мысль этого произведения?"
        else:
            text = "Пожалуйста, скажите «Вопрос-ответ» или «Сократический диалог»."
            buttons = [{"title": "Вопрос-ответ", "hide": True}, {"title": "Сократический диалог", "hide": True}]
            
    elif session_state.get("step") == "QA_LOOP":
        book = session_state.get('book')
        sys_prompt = PROMPT_QA.format(book=book)
        
        llm_response = await ask_yandexgpt(sys_prompt, original_utterance)
        text = llm_response
        
        # Увеличиваем счетчик после успешного запроса
        new_user_state["daily_requests"] = current_requests + 1
        
    elif session_state.get("step") == "SOCRATIC_LOOP":
        book = session_state.get('book')
        current_step = session_state.get("socratic_step", 1)
        
        sys_prompt = PROMPT_SOCRATIC.format(book=book, step=current_step)
        
        llm_response = await ask_yandexgpt(sys_prompt, original_utterance)
        text = llm_response
        
        # Увеличиваем счетчик после успешного запроса
        new_user_state["daily_requests"] = current_requests + 1
        new_session_state["socratic_step"] = current_step + 1
        
        if new_session_state["socratic_step"] > 3:
            text += ("\n\n(Мы завершили разбор. Вы уверенно анализируете мотивы, "
                     "но стоит подтянуть контекст эпохи. До встреч!)")
            end_session = True
            new_session_state["step"] = "END"
            
    else:
        text = "Давайте начнем сначала. Какую книгу будем разбирать?"
        new_session_state = {"step": "CHOOSE_BOOK"}
        buttons = [{"title": b, "hide": True} for b in BOOKS]

    if len(text) > 1024:
        text = text[:1021] + "..."

    return {
        "response": {
            "text": text,
            "tts": tts or text,
            "buttons": buttons,
            "end_session": end_session
        },
        "session_state": new_session_state,
        "user_state_update": new_user_state,  # Сохраняем стейт юзера (лимиты)
        "version": "1.0"
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)