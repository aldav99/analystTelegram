# create_session.py - Скрипт для создания сессии локально
import os
from telethon import TelegramClient

async def create_session():
    """Создание файла сессии локально"""
    api_id = input("Введите API ID: ")
    api_hash = input("Введите API Hash: ")
    phone = input("Введите номер телефона: ")
    
    client = TelegramClient("telegram_analyzer_session", api_id, api_hash)
    
    await client.start(phone)
    print("Сессия успешно создана!")
    
    # Получаем строковую сессию для загрузки в переменную окружения
    session_string = client.session.save()
    print(f"Session string: {session_string}")
    
    await client.disconnect()

if __name__ == "__main__":
    import asyncio
    asyncio.run(create_session())
