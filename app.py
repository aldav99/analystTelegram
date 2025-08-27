"""
🚀 Telegram Channel Analyzer FastAPI - Simplified Version
========================================================
Веб-сервис для анализа Telegram каналов с помощью FastAPI.
Извлекает статистику сообщений: просмотры, реакции, пересылки и текст постов.
Без комментариев - только основные посты канала.
Автор: AI Assistant
Дата: 2025
"""
import asyncio
import os
import json
import logging
import base64
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

# FastAPI и Pydantic импорты
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# Telegram API импорты
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel
from telethon.errors import SessionPasswordNeededError, FloodWaitError, ChannelPrivateError

# Uvicorn для запуска сервера
import uvicorn

# ==============================================================================
# 🔧 КОНФИГУРАЦИЯ И НАСТРОЙКИ
# ==============================================================================

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Глобальные настройки
LIMIT_MESSAGES = int(os.getenv("LIMIT_MESSAGES", "200"))  # Лимит сообщений
DAYS_BACK = int(os.getenv("DAYS_BACK", "90"))            # Период анализа в днях
SESSION_NAME = "telegram_analyzer_session"                # Имя файла сессии
SESSION_FILE = f"{SESSION_NAME}.session"                  # Полный путь к файлу сессии

# Глобальная переменная для хранения клиента
telegram_client: Optional[TelegramClient] = None

# ==============================================================================
# 📋 PYDANTIC МОДЕЛИ ДЛЯ API
# ==============================================================================

class ChannelAnalysisRequest(BaseModel):
    """Модель запроса для анализа канала"""
    channel_username: str = Field(
        ..., 
        description="Username канала (например: @DavBlog или DavBlog)",
        min_length=1,
        max_length=100
    )
    limit_messages: Optional[int] = Field(
        default=LIMIT_MESSAGES,
        description="Максимальное количество сообщений для анализа",
        ge=1,
        le=1000
    )
    days_back: Optional[int] = Field(
        default=DAYS_BACK,
        description="Количество дней назад для поиска сообщений",
        ge=1,
        le=365
    )

    @validator('channel_username')
    def validate_channel_username(cls, v):
        """Валидация имени канала"""
        v = v.strip()
        if not v:
            raise ValueError("Имя канала не может быть пустым")
        
        # Удаляем @ если он есть в начале
        if v.startswith('@'):
            v = v[1:]
        
        # Проверяем корректность имени канала
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError("Имя канала содержит недопустимые символы")
            
        return v

class PostInfo(BaseModel):
    """Информация о посте"""
    date: str
    type: str
    views: int
    reactions: int
    forwards: int
    content: str
    url: str

class ChannelAnalysisResponse(BaseModel):
    """Ответ анализа канала"""
    success: bool
    channel_title: str
    channel_username: Optional[str]
    channel_id: int
    subscribers_count: Optional[int]
    analysis_period: str
    total_messages_analyzed: int
    posts: Dict[str, PostInfo]
    analysis_timestamp: str

class HealthResponse(BaseModel):
    """Ответ проверки здоровья сервиса"""
    status: str
    timestamp: str
    telegram_client_status: str
    version: str = "1.0.0"

# ==============================================================================
# 🔐 УПРАВЛЕНИЕ КРЕДЕНЦИАЛАМИ TELEGRAM
# ==============================================================================

def get_telegram_credentials() -> tuple[str, str, str]:
    """
    Получение учетных данных Telegram из переменных окружения
    
    Returns:
        tuple: (api_id, api_hash, phone)
        
    Raises:
        ValueError: Если не все обязательные переменные установлены
    """
    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH') 
    phone = os.getenv('TELEGRAM_PHONE')
    
    missing_vars = []
    if not api_id:
        missing_vars.append('TELEGRAM_API_ID')
    if not api_hash:
        missing_vars.append('TELEGRAM_API_HASH')
    if not phone:
        missing_vars.append('TELEGRAM_PHONE')
    
    if missing_vars:
        raise ValueError(
            f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}\n"
            f"Установите переменные:\n"
            f"- TELEGRAM_API_ID: ваш API ID\n"
            f"- TELEGRAM_API_HASH: ваш API Hash\n" 
            f"- TELEGRAM_PHONE: номер телефона в формате +79991234567"
        )
    
    logger.info(f"Креденциалы получены для номера: {phone[:4]}****{phone[-4:]}")
    return api_id, api_hash, phone

# ==============================================================================
# 🤖 УПРАВЛЕНИЕ TELEGRAM КЛИЕНТОМ
# ==============================================================================

async def init_telegram_client() -> TelegramClient:
    """
    Инициализация и авторизация Telegram клиента.
    
    Returns:
        TelegramClient: Авторизованный клиент
        
    Raises:
        Exception: При ошибках инициализации или авторизации
    """
    try:
        api_id, api_hash, phone = get_telegram_credentials()
        
        # Проверяем, существует ли файл сессии
        session_exists = os.path.exists(SESSION_FILE)
        
        # Если файла нет, пытаемся воссоздать его из переменной окружения
        if not session_exists:
            session_b64 = os.getenv('TELEGRAM_SESSION_BASE64')
            if session_b64:
                try:
                    # Декодируем base64 и записываем в файл
                    session_data = base64.b64decode(session_b64)
                    with open(SESSION_FILE, 'wb') as f:
                        f.write(session_data)
                    logger.info("Файл сессии воссоздан из переменной окружения")
                    session_exists = True
                except Exception as e:
                    logger.error(f"Ошибка декодирования сессии: {e}")
        
        # Создаем клиент
        client = TelegramClient(SESSION_NAME, int(api_id), api_hash)
        
        # Подключаемся с обработкой 2FA
        password = os.getenv('TELEGRAM_2FA_PASSWORD')
        if password:
            await client.start(phone, password=password)
        else:
            await client.start(phone)
        
        # Проверяем авторизацию
        me = await client.get_me()
        logger.info(f"Успешно авторизован как: {me.first_name} {me.last_name or ''} (ID: {me.id})")
        
        return client
        
    except SessionPasswordNeededError:
        raise Exception(
            "Требуется двухфакторная аутентификация (пароль). "
            "Установите переменную окружения TELEGRAM_2FA_PASSWORD."
        )
    except Exception as e:
        logger.error(f"Ошибка инициализации Telegram клиента: {e}")
        # Удаляем поврежденный файл сессии
        if os.path.exists(SESSION_FILE) and ("auth" in str(e).lower() or "invalid" in str(e).lower()):
            try:
                os.remove(SESSION_FILE)
                logger.info("Поврежденный файл сессии удален.")
            except Exception as rm_error:
                logger.warning(f"Не удалось удалить поврежденный файл сессии: {rm_error}")
        raise Exception(f"Не удалось инициализировать Telegram клиент: {str(e)}")

async def get_telegram_client() -> TelegramClient:
    """
    Получение активного Telegram клиента с проверкой подключения
    
    Returns:
        TelegramClient: Активный клиент
    """
    global telegram_client
    
    if telegram_client is None or not telegram_client.is_connected():
        telegram_client = await init_telegram_client()
    
    return telegram_client

# ==============================================================================
# 📊 ОСНОВНЫЕ ФУНКЦИИ АНАЛИЗА
# ==============================================================================

async def find_channel(client: TelegramClient, channel_username: str) -> Channel:
    """
    Поиск канала по имени пользователя
    
    Args:
        client: Telegram клиент
        channel_username: Имя пользователя канала (сохраняет регистр)
        
    Returns:
        Channel: Найденный канал
        
    Raises:
        HTTPException: При ошибках поиска канала
    """
    try:
        # Сохраняем оригинальное имя для точного поиска и отображения
        original_username = channel_username
        
        # Для поиска используем версию в нижнем регистре (Telegram всегда возвращает lowercase)
        search_username = original_username.lower()
        
        logger.info(f"Ищем канал: {original_username} (поиск: @{search_username})")
        
        # Пробуем найти канал через get_entity
        try:
            target_channel = await client.get_entity(f"@{search_username}")
        except ValueError:
            # Если не нашли по username, пробуем поиск по всему Telegram
            logger.info(f"Канал @{search_username} не найден, пробуем поиск...")
            search_results = await client.get_dialogs()
            
            for dialog in search_results:
                if (hasattr(dialog.entity, 'username') and 
                    dialog.entity.username and 
                    dialog.entity.username.lower() == search_username):
                    target_channel = dialog.entity
                    break
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Канал @{original_username} не найден"
                )
        
        # Проверяем что это именно канал
        if not isinstance(target_channel, Channel):
            raise HTTPException(
                status_code=400,
                detail=f"@{original_username} не является каналом"
            )
        
        # Проверяем совпадение username (в нижнем регистре)
        actual_username = getattr(target_channel, 'username', '')
        if actual_username.lower() != search_username:
            logger.warning(f"Найден канал @{actual_username} вместо @{original_username}")
            raise HTTPException(
                status_code=404,
                detail=f"Канал @{original_username} не найден. Найден канал @{actual_username}"
            )
        
        logger.info(f"Канал найден: {target_channel.title} (@{actual_username})")
        return target_channel
        
    except ChannelPrivateError:
        raise HTTPException(
            status_code=403,
            detail=f"Канал @{channel_username} приватный или недоступен"
        )
    except HTTPException:
        raise  # Перебрасываем HTTP исключения как есть
    except Exception as e:
        logger.error(f"Ошибка поиска канала @{channel_username}: {e}")
        raise HTTPException(
            status_code=404,
            detail=f"Канал @{channel_username} не найден: {str(e)}"
        )

async def get_channel_info(client: TelegramClient, channel: Channel, original_username: str = None) -> dict:
    """
    Получение информации о канале
    
    Args:
        client: Telegram клиент
        channel: Канал для анализа
        original_username: Оригинальное имя пользователя (для сохранения регистра)
        
    Returns:
        dict: Информация о канале
    """
    # Используем оригинальный username из запроса для отображения
    # Telegram всегда возвращает username в нижнем регистре, но мы сохраняем оригинальный регистр
    display_username = original_username if original_username else getattr(channel, 'username', None)
    
    info = {
        'title': channel.title,
        'username': display_username,
        'id': channel.id,
        'subscribers_count': None
    }
    
    try:
        full_info = await client(GetFullChannelRequest(channel))
        info['subscribers_count'] = full_info.full_chat.participants_count
        logger.info(f"Получена информация о канале: {info['subscribers_count']} подписчиков")
    except Exception as e:
        logger.warning(f"Не удалось получить полную информацию о канале: {e}")
        info['subscribers_count'] = getattr(channel, 'participants_count', None)
    
    return info

# ==============================================================================

async def get_channel_messages(client: TelegramClient, channel: Channel, limit: int, days_back: int) -> list:
    """
    Получение основных постов канала
    
    Args:
        client: Telegram клиент
        channel: Канал
        limit: Лимит сообщений
        days_back: Дней назад для поиска (игнорируется)
        
    Returns:
        list: Список основных постов канала
    """
    logger.info(f"Загружаем до {limit} последних сообщений")
    
    try:
        # Получаем последние сообщения без фильтрации по дате
        all_messages = await client.get_messages(channel, limit=limit)
        logger.info(f"Всего найдено {len(all_messages)} сообщений")
        
        # Возвращаем все сообщения без фильтрации
        logger.info(f"Возвращаем все {len(all_messages)} сообщений без фильтрации")
        return all_messages
        
    except FloodWaitError as e:
        logger.error(f"Превышен лимит запросов. Нужно подождать {e.seconds} секунд")
        raise HTTPException(
            status_code=429,
            detail=f"Превышен лимит запросов Telegram. Повторите через {e.seconds} секунд"
        )
    except Exception as e:
        logger.error(f"Ошибка получения сообщений: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка получения сообщений: {str(e)}"
        )

# ==============================================================================

def get_media_type(media) -> str:
    """Определение типа медиа"""
    media_type = str(type(media).__name__)
    
    if 'Photo' in media_type:
        return "Фото"
    elif 'Video' in media_type or 'Document' in media_type:
        return "Видео/Документ"
    elif 'Audio' in media_type:
        return "Аудио"
    elif 'Sticker' in media_type:
        return "Стикер"
    elif 'Poll' in media_type:
        return "Опрос"
    elif 'WebPage' in media_type:
        return "Веб-страница"
    else:
        return "Медиа"
# ==============================================================================

def process_channel_posts(messages: list, channel: Channel) -> dict:
    """
    Обработка постов канала и получение статистики
    
    Args:
        messages: Список постов канала
        channel: Канал
        
    Returns:
        dict: Обработанные данные постов
    """
    posts_data = {}
    processed_count = 0
    
    logger.info(f"Начинаем обработку {len(messages)} постов")
    
    for i, msg in enumerate(reversed(messages), 1):
        try:
            # Диагностика: логируем информацию о сообщении
            logger.debug(f"Сообщение {i}: ID={msg.id}, Дата={msg.date}, Текст={getattr(msg, 'message', 'N/A')[:50]}...")
            
            # Определяем тип поста и содержание
            post_type = "Текст"
            content = ""
            
            # Извлекаем текст сообщения
            if hasattr(msg, 'message') and msg.message:
                content = msg.message.strip()
                logger.debug(f"Найден текст: {content[:50]}...")
            
            # Определяем тип медиа, если есть
            if hasattr(msg, 'media') and msg.media:
                media_type = get_media_type(msg.media)
                post_type = media_type
                logger.debug(f"Найден медиа: {media_type}")
                if content:
                    content = f"[{media_type}] {content}"
                else:
                    content = f"[{media_type}]"
            
            # Если нет ни текста, ни медиа, помечаем как пустой
            if not content:
                content = "[Пустой пост]"
                logger.debug("Пост без текста и медиа")
            
            # Получаем статистику поста
            views_count = getattr(msg, 'views', 0) or 0
            logger.debug(f"Просмотры: {views_count}")
            
            # Получаем количество реакций
            reactions_count = 0
            try:
                if hasattr(msg, 'reactions') and msg.reactions and hasattr(msg.reactions, 'results'):
                    if msg.reactions.results:
                        reactions_count = sum([r.count for r in msg.reactions.results if hasattr(r, 'count') and r.count])
            except Exception as e:
                logger.warning(f"Ошибка получения реакций для поста {msg.id}: {e}")
            logger.debug(f"Реакции: {reactions_count}")
            
            # Получаем количество пересылки
            forwards_count = getattr(msg, 'forwards', 0) or 0
            logger.debug(f"Пересылки: {forwards_count}")
            
            # Форматируем дату
            msg_date = msg.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            formatted_date = msg_date.strftime("%Y-%m-%d %H:%M:%S")
            
            # Создаем ссылку на пост
            channel_username = getattr(channel, 'username', None)
            post_link = ""
            if channel_username:
                post_link = f"https://t.me/{channel_username}/{msg.id}"
            else:
                post_link = f"Пост #{msg.id}"
            
            # Добавляем данные поста
            posts_data[str(msg.id)] = {
                'date': formatted_date,
                'type': post_type,
                'views': views_count,
                'reactions': reactions_count,
                'forwards': forwards_count,
                'content': content[:1000],
                'url': post_link
            }
            
            processed_count += 1
            logger.debug(f"Пост {msg.id} успешно обработан")
            
        except Exception as e:
            logger.error(f"Ошибка обработки поста {msg.id}: {e}")
            continue
    
    logger.info(f"Успешно обработано постов: {processed_count}/{len(messages)}")
    return posts_data

# ==============================================================================
# 🌐 LIFESPAN MANAGEMENT
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    logger.info("🚀 Запуск Telegram Channel Analyzer API")
    
    # Инициализация при старте
    try:
        global telegram_client
        telegram_client = await init_telegram_client()
        logger.info("✅ Telegram клиент успешно инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Telegram клиента: {e}")
    
    yield
    
    # Очистка при завершении
    if telegram_client:
        await telegram_client.disconnect()
        logger.info("🔐 Telegram клиент отключен")

# ==============================================================================
# 🌍 СОЗДАНИЕ FASTAPI ПРИЛОЖЕНИЯ
# ==============================================================================

app = FastAPI(
    title="Telegram Channel Analyzer",
    description="🤖 API для анализа статистики Telegram каналов",
    version="1.0.0",
    lifespan=lifespan
)

# ==============================================================================
# 📍 API ЭНДПОИНТЫ
# ==============================================================================

@app.get("/", response_model=HealthResponse)
async def root():
    """Корневой эндпоинт с информацией о сервиса"""
    return HealthResponse(
        status="running",
        timestamp=datetime.now(timezone.utc).isoformat(),
        telegram_client_status="connected" if telegram_client and telegram_client.is_connected() else "disconnected"
    )

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка работоспособности сервиса"""
    client_status = "disconnected"
    
    try:
        if telegram_client and telegram_client.is_connected():
            await telegram_client.get_me()
            client_status = "connected"
    except Exception as e:
        logger.error(f"Ошибка проверки Telegram клиента: {e}")
        client_status = "error"
    
    return HealthResponse(
        status="healthy" if client_status == "connected" else "unhealthy",
        timestamp=datetime.now(timezone.utc).isoformat(),
        telegram_client_status=client_status
    )

@app.post("/analyze", response_model=ChannelAnalysisResponse)
async def analyze_channel(request: ChannelAnalysisRequest):
    """
    Анализ Telegram канала
    
    Извлекает статистику постов канала:
    - Просмотры
    - Реакции
    - Пересылки
    - Полный текст постов
    - Тип контента
    
    Args:
        request: Параметры запроса анализа
        
    Returns:
        ChannelAnalysisResponse: Результат анализа
        
    Example:
        POST /analyze
        {
            "channel_username": "@DavBlog",
            "limit_messages": 50,
            "days_back": 7
        }
    """
    start_time = datetime.now(timezone.utc)
    logger.info(f"Начинаем анализ канала: @{request.channel_username}")
    
    try:
        # Получаем клиент
        client = await get_telegram_client()
        
        # Находим канал (сохраняем оригинальное имя)
        channel = await find_channel(client, request.channel_username)
        
        # Получаем информацию о канале, передавая оригинальное имя для сохранения регистра
        channel_info = await get_channel_info(client, channel, request.channel_username)
        
        # Получаем основные посты канала
        messages = await get_channel_messages(
            client, 
            channel, 
            request.limit_messages, 
            request.days_back
        )
        
        logger.info(f"Получено {len(messages)} сообщений для обработки")
        
        if not messages:
            logger.warning("Не найдено ни одного сообщения для анализа")
            return ChannelAnalysisResponse(
                success=True,
                channel_title=channel_info['title'],
                channel_username=channel_info['username'],
                channel_id=channel_info['id'],
                subscribers_count=channel_info['subscribers_count'],
                analysis_period=f"{request.days_back} дней",
                total_messages_analyzed=0,
                posts={},
                analysis_timestamp=start_time.isoformat()
            )
        
        # Обрабатываем посты
        posts_data = process_channel_posts(messages, channel)
        
        end_time = datetime.now(timezone.utc)
        processing_time = (end_time - start_time).total_seconds()
        
        logger.info(f"Анализ завершен за {processing_time:.2f} секунд. Обработано {len(posts_data)} постов")
        
        return ChannelAnalysisResponse(
            success=True,
            channel_title=channel_info['title'],
            channel_username=channel_info['username'],  # Сохраняем оригинальный регистр
            channel_id=channel_info['id'],
            subscribers_count=channel_info['subscribers_count'],
            analysis_period=f"{request.days_back} дней",
            total_messages_analyzed=len(posts_data),
            posts=posts_data,
            analysis_timestamp=start_time.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Неожиданная ошибка анализа: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Внутренняя ошибка сервера: {str(e)}"
        )

@app.get("/status")
async def get_status():
    """Получение расширенного статуса сервиса"""
    try:
        client_info = {}
        
        if telegram_client and telegram_client.is_connected():
            try:
                me = await telegram_client.get_me()
                client_info = {
                    "connected": True,
                    "user_id": me.id,
                    "username": me.username,
                    "first_name": me.first_name,
                    "phone": me.phone
                }
            except Exception as e:
                client_info = {"connected": False, "error": str(e)}
        else:
            client_info = {"connected": False, "reason": "Client not initialized"}
        
        return {
            "service": "Telegram Channel Analyzer",
            "version": "1.0.0",
            "status": "running",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "telegram_client": client_info,
            "settings": {
                "default_limit_messages": LIMIT_MESSAGES,
                "default_days_back": DAYS_BACK
            }
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "service": "Telegram Channel Analyzer",
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )

# ==============================================================================
# 🚀 ТОЧКА ВХОДА ПРИЛОЖЕНИЯ
# ==============================================================================

if __name__ == "__main__":
    # Настройка для локального запуска
    port = int(os.environ.get("PORT", 8000))
    
    logger.info(f"🚀 Запуск сервера на порту {port}")
    logger.info(f"📖 Документация API доступна по адресу: http://localhost:{port}/docs")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        reload=False
    )
