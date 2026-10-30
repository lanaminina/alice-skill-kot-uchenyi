import asyncio
import os
import sys
from httpx import AsyncClient, ASGITransport
from main import app, VOICE_SPEAKER

async def run_tests():
    transport = ASGITransport(app=app)
    voice_tag = f'<speaker voice="{VOICE_SPEAKER}">'
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Healthcheck / Ping
        res = await ac.post("/webhook", json={"request": {"original_utterance": "ping"}})
        assert res.status_code == 200, f"Ping failed: {res.status_code}"
        assert res.json()["response"]["text"] == "pong"
        print("[OK] Ping -> pong")

        # 2. Новая сессия (старт)
        res_new = await ac.post("/webhook", json={
            "session": {"new": True, "session_id": "test_1"},
            "request": {"command": "", "original_utterance": ""},
            "state": {"session": {}, "user": {}}
        })
        d_new = res_new.json()
        assert res_new.status_code == 200
        assert "Кот Учёный" in d_new["response"]["text"]
        assert voice_tag in d_new["response"]["tts"]
        assert len(d_new["response"]["buttons"]) == 3
        assert d_new["session_state"]["step"] == "CHOOSE_BOOK"
        print(f"[OK] Start session -> Кот Ученый с голосом {VOICE_SPEAKER} и выбором 3 книг")

        # 3. Выбор книги
        res_book = await ac.post("/webhook", json={
            "session": {"new": False, "session_id": "test_1"},
            "request": {"command": "Гроза", "original_utterance": "Гроза"},
            "state": {"session": {"step": "CHOOSE_BOOK"}, "user": {"daily_requests": 0}}
        })
        d_book = res_book.json()
        assert "Гроза" in d_book["response"]["text"]
        assert d_book["session_state"]["step"] == "CHOOSE_MODE"
        print("[OK] Choose book -> выбор режима («Вопрос-ответ» или «Сократический диалог»)")

        # 4. Интент помощи
        res_help = await ac.post("/webhook", json={
            "session": {"new": False, "session_id": "test_1"},
            "request": {"command": "помощь", "original_utterance": "что ты умеешь"},
            "state": {"session": {"step": "CHOOSE_MODE"}, "user": {}}
        })
        d_help = res_help.json()
        assert "проводник и собеседник" in d_help["response"]["text"]
        print("[OK] Help intent -> корректная подсказка")

        # 5. Режим вопроса с ответом по Грозе (демо/мок)
        res_qa = await ac.post("/webhook", json={
            "session": {"new": False, "session_id": "test_1"},
            "request": {"command": "В чем смысл финала Катерины?", "original_utterance": "В чем смысл финала Катерины?"},
            "state": {"session": {"step": "QA_LOOP", "book": "Гроза"}, "user": {"daily_requests": 0}}
        })
        d_qa = res_qa.json()
        assert len(d_qa["response"]["text"]) > 20
        assert voice_tag in d_qa["response"]["tts"]
        print("[OK] Q&A response -> получен филологический ответ с TTS")

    print("\n=== ВСЕ ТЕСТЫ ВЕБХУКА УСПЕШНО ПРОЙДЕНЫ ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
