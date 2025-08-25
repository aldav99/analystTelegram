# main.py
"""
🚀 Telegram Channel Analyzer FastAPI
====================================
Веб-сервис для анализа Telegram каналов с помощью FastAPI.
Извлекает статистику сообщений: просмотры, комментарии, реакции, пересылки.
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
DAYS_BACK = int(os.getenv("DAYS_BACK", "30"))            # Период анализа в днях
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
        description="Username канала (например: @mychannel или mychannel)",
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
    comments: int
    reactions: int
    forwards: int
    content: str
    url: str

class Comment(BaseModel):
    """Информация о комментарии"""
    author: str
    date: str
    text: str

class MessageData(BaseModel):
    """Данные сообщения"""
    post_info: PostInfo
    comments: List[Comment]

class ChannelAnalysisResponse(BaseModel):
    """Ответ анализа канала"""
    success: bool
    channel_title: str
    channel_username: Optional[str]
    channel_id: int
    subscribers_count: Optional[int]
    analysis_period: str
    total_messages_analyzed: int
    messages: Dict[str, MessageData]
    analysis_timestamp: str

class HealthResponse(BaseModel):
    """Ответ проверки здоровья сервиса"""
    status: str
    timestamp: str
    telegram_client_status: str
    version: str = "1.0.0"

class ErrorResponse(BaseModel):
    """Модель ответа об ошибке"""
    success: bool = False
    error: str
    error_code: str
    timestamp: str

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
    Пытается использовать существующую сессию или воссоздать её из TELEGRAM_SESSION_BASE64.
    Если сессия недействительна или отсутствует, начинает новую авторизацию.
    
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
                    logger.info("Файл сессии воссоздан из переменной окружения TELEGRAM_SESSION_BASE64")
                    session_exists = True
                except Exception as e:
                    logger.error(f"Ошибка декодирования или записи файла сессии из переменной окружения: {e}")
                    # Продолжаем, возможно, начнется новая авторизация
        
        # Создаем клиент
        client = TelegramClient(SESSION_NAME, int(api_id), api_hash)
        
        # Подключаемся. Если сессия валидна, авторизация пройдет автоматически.
        # Если файла не было и не удалось воссоздать, или сессия невалидна, начнется новая авторизация.
        # Обрабатываем 2FA если нужно
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
        # Удаляем поврежденный файл сессии, если ошибка указывает на проблему с сессией
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
        channel_username: Имя пользователя канала
        
    Returns:
        Channel: Найденный канал
        
    Raises:
        HTTPException: При ошибках поиска канала
    """
    try:
        # Добавляем @ если его нет
        if not channel_username.startswith('@'):
            channel_username = f'@{channel_username}'
        
        target_channel = await client.get_entity(channel_username)
        
        # Проверяем что это именно канал
        if not isinstance(target_channel, Channel):
            raise HTTPException(
                status_code=400,
                detail=f"{channel_username} не является каналом"
            )
        
        logger.info(f"Канал найден: {target_channel.title}")
        return target_channel
        
    except ChannelPrivateError:
        raise HTTPException(
            status_code=403,
            detail=f"Канал {channel_username} приватный или недоступен"
        )
    except Exception as e:
        logger.error(f"Ошибка поиска канала {channel_username}: {e}")
        raise HTTPException(
            status_code=404,
            detail=f"Канал {channel_username} не найден: {str(e)}"
        )

async def get_channel_info(client: TelegramClient, channel: Channel) -> dict:
    """
    Получение информации о канале
    
    Args:
        client: Telegram клиент
        channel: Канал для анализа
        
    Returns:
        dict: Информация о канале
    """
    info = {
        'title': channel.title,
        'username': getattr(channel, 'username', None),
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

async def get_messages(client: TelegramClient, channel: Channel, limit: int, days_back: int) -> list:
    """
    Получение сообщений из канала (ТОЛЬКО основные посты канала)
    
    Args:
        client: Telegram клиент
        channel: Канал
        limit: Лимит сообщений
        days_back: Дней назад для поиска
        
    Returns:
        list: Список сообщений-постов из канала
    """
    offset_date = datetime.now(timezone.utc) - timedelta(days=days_back)
    
    logger.info(f"Загружаем до {limit} сообщений за последние {days_back} дней")
    
    try:
        # Получаем сообщения из самого канала
        all_messages = await client.get_messages(channel, limit=limit)
        logger.info(f"Всего найдено {len(all_messages)} сообщений")
        
        # ИСПРАВЛЕНИЕ: Фильтруем только основные посты канала
        # Основные посты канала имеют:
        # 1. reply_to_msg_id == None (не являются ответами/комментариями)
        # 2. from_id == None (отправлены от имени канала)
        # 3. post_author == None (не из связанной группы обсуждений)
        
        channel_posts = []
        for msg in all_messages:
            msg_date = msg.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            
            # Проверяем дату и что это основной пост канала
            is_channel_post = (
                msg_date >= offset_date and  # В нужном периоде
                msg.reply_to_msg_id is None and  # Не является ответом/комментарием
                msg.from_id is None and  # Отправлен от имени канала
                msg.post_author is None  # Не из связанной группы обсуждений
            )
            
            if is_channel_post:
                channel_posts.append(msg)
        
        logger.info(f"После фильтрации основных постов канала: {len(channel_posts)} сообщений")
        return channel_posts
        
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

def get_media_type(media) -> str:
    """
    Определение типа медиа
    
    Args:
        media: Медиа объект
        
    Returns:
        str: Тип медиа
    """
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

async def get_discussion_group(client: TelegramClient, channel: Channel) -> Optional[Channel]:
    """
    Получение связанной группы обсуждений для канала
    
    Args:
        client: Telegram клиент
        channel: Канал
        
    Returns:
        Channel или None: Связанная группа обсуждений если есть
    """
    try:
        full_channel = await client(GetFullChannelRequest(channel))
        linked_chat_id = getattr(full_channel.full_chat, 'linked_chat_id', None)
        
        if linked_chat_id:
            discussion_group = await client.get_entity(linked_chat_id)
            logger.info(f"Найдена связанная группа обсуждений: {discussion_group.title}")
            return discussion_group
        else:
            logger.info("У канала нет связанной группы обсуждений")
            return None
            
    except Exception as e:
        logger.warning(f"Не удалось получить связанную группу обсуждений: {e}")
        return None

async def get_post_comments(client: TelegramClient, channel: Channel, discussion_group: Optional[Channel], post_id: int, limit: int = 10) -> List[dict]:
    """
    Получение комментариев к конкретному посту
    
    Args:
        client: Telegram клиент
        channel: Основной канал
        discussion_group: Группа обсуждений (может быть None)
        post_id: ID поста в канале
        limit: Максимальное количество комментариев
        
    Returns:
        List[dict]: Список комментариев
    """
    comments_data = []
    
    if not discussion_group:
        return comments_data
    
    try:
        # Ищем комментарии в группе обсуждений
        # Комментарии в группе обсуждений ссылаются на оригинальный пост через reply_to_msg_id
        comment_count = 0
        
        async for comment in client.iter_messages(discussion_group, limit=100):
            if comment_count >= limit:
                break
                
            # ИСПРАВЛЕНИЕ: Проверяем что комментарий относится к нашему посту
            if (hasattr(comment, 'reply_to_msg_id') and 
                comment.reply_to_msg_id == post_id and
                comment.from_id is not None):  # Комментарий от пользователя
                
                comment_date = comment.date
                if comment_date.tzinfo is None:
                    comment_date = comment_date.replace(tzinfo=timezone.utc)
                formatted_date = comment_date.strftime("%Y-%m-%d %H:%M:%S")
                
                comment_text = comment.message if comment.message else "[Нет текста]"
                
                # Обрезаем длинные комментарии
                if len(comment_text) > 100:
                    comment_text = comment_text[:100] + "..."
                
                # Получаем автора комментария
                author_info = "Unknown"
                if comment.from_id:
                    try:
                        author = await client.get_entity(comment.from_id)
                        if hasattr(author, 'username') and author.username:
                            author_info = f"@{author.username}"
                        elif hasattr(author, 'first_name'):
                            author_info = author.first_name
                            if hasattr(author, 'last_name') and author.last_name:
                                author_info += f" {author.last_name}"
                        else:
                            author_info = f"User_{comment.from_id.user_id}"
                    except Exception as e:
                        logger.warning(f"Не удалось получить информацию об авторе комментария: {e}")
                        author_info = f"User_{comment.from_id.user_id if hasattr(comment.from_id, 'user_id') else 'Unknown'}"
                
                comments_data.append({
                    'author': author_info,
                    'date': formatted_date,
                    'text': comment_text
                })
                comment_count += 1
                
    except Exception as e:
        logger.warning(f"Ошибка получения комментариев для поста {post_id}: {e}")
    
    logger.info(f"Найдено {len(comments_data)} комментариев для поста {post_id}")
    return comments_data

async def process_messages(client: TelegramClient, messages: list, channel: Channel) -> dict:
    """
    Обработка сообщений и получение статистики включая комментарии
    
    Args:
        client: Telegram клиент
        messages: Список сообщений-постов из канала
        channel: Канал
        
    Returns:
        dict: Обработанные данные сообщений
    """
    data = {}
    processed_count = 0
    
    logger.info(f"Начинаем обработку {len(messages)} сообщений")
    
    # Получаем связанную группу обсуждений один раз
    discussion_group = await get_discussion_group(client, channel)
    
    for i, msg in enumerate(reversed(messages), 1):
        try:
            # Определяем тип сообщения и содержание
            msg_type = "текст"
            content = ""
            
            if hasattr(msg, 'message') and msg.message:
                content = msg.message[:200] + "..." if len(msg.message) > 200 else msg.message
            elif hasattr(msg, 'media') and msg.media:
                msg_type = get_media_type(msg.media)
                content = f"[{msg_type}]"
            else:
                msg_type = "прочее"
                content = "[Служебное сообщение]"
            
            # Получаем статистику сообщения
            views_count = getattr(msg, 'views', 0) or 0
            replies_count = 0
            if hasattr(msg, 'replies') and msg.replies:
                replies_count = getattr(msg.replies, 'replies', 0) or 0
            
            # Получаем количество реакций
            reactions_count = 0
            try:
                if hasattr(msg, 'reactions') and msg.reactions and msg.reactions.results:
                    reactions_count = sum([r.count for r in msg.reactions.results if r.count])
            except:
                reactions_count = 0
            
            # Получаем количество пересылок
            forwards_count = getattr(msg, 'forwards', 0) or 0
            
            # Форматируем дату
            msg_date = msg.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            formatted_date = msg_date.strftime("%Y-%m-%d %H:%M:%S")
            
            # Создаем ссылку на сообщение
            channel_username = getattr(channel, 'username', None)
            message_link = ""
            if channel_username:
                message_link = f"https://t.me/{channel_username}/{msg.id}"
            else:
                message_link = f"Сообщение #{msg.id}"
            
            # ИСПРАВЛЕНИЕ: Получение комментариев из связанной группы обсуждений
            comments_data = []
            if replies_count > 0 and discussion_group:
                comments_data = await get_post_comments(client, channel, discussion_group, msg.id, limit=10)
            
            # Добавляем данные сообщения
            data[str(msg.id)] = {
                'post_info': {
                    'date': formatted_date,
                    'type': msg_type,
                    'views': views_count,
                    'comments': replies_count,
                    'reactions': reactions_count,
                    'forwards': forwards_count,
                    'content': content,
                    'url': message_link
                },
                'comments': comments_data
            }
            
            processed_count += 1
            
            # Логируем прогресс каждые 10 сообщений
            if i % 10 == 0:
                logger.info(f"Обработано {i}/{len(messages)} сообщений")
                
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения {msg.id}: {e}")
            continue
    
    logger.info(f"Обработано сообщений: {processed_count}")
    return data

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
        # Не завершаем приложение, пусть работает, но без клиента
        # Пользователь сможет увидеть статус через /health
    
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
    """Корневой эндпоинт с информацией о сервисе"""
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
            # Проверяем подключение к Telegram
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
    
    Извлекает статистику сообщений канала включая:
    - Просмотры
    - Комментарии  
    - Реакции
    - Пересылки
    - Содержимое постов
    
    Args:
        request: Параметры запроса анализа
        
    Returns:
        ChannelAnalysisResponse: Результат анализа
    """
    start_time = datetime.now(timezone.utc)
    logger.info(f"Начинаем анализ канала: @{request.channel_username}")
    
    try:
        # Получаем клиент
        client = await get_telegram_client()
        
        # Находим канал
        channel = await find_channel(client, request.channel_username)
        
        # Получаем информацию о канале
        channel_info = await get_channel_info(client, channel)
        
        # Получаем сообщения (только основные посты из канала)
        messages = await get_messages(
            client, 
            channel, 
            request.limit_messages, 
            request.days_back
        )
        
        if not messages:
            return ChannelAnalysisResponse(
                success=True,
                channel_title=channel_info['title'],
                channel_username=channel_info['username'],
                channel_id=channel_info['id'],
                subscribers_count=channel_info['subscribers_count'],
                analysis_period=f"{request.days_back} дней",
                total_messages_analyzed=0,
                messages={},
                analysis_timestamp=start_time.isoformat()
            )
        
        # Обрабатываем сообщения
        processed_data = await process_messages(client, messages, channel)
        
        end_time = datetime.now(timezone.utc)
        processing_time = (end_time - start_time).total_seconds()
        
        logger.info(f"Анализ завершен за {processing_time:.2f} секунд")
        
        return ChannelAnalysisResponse(
            success=True,
            channel_title=channel_info['title'],
            channel_username=channel_info['username'],
            channel_id=channel_info['id'],
            subscribers_count=channel_info['subscribers_count'],
            analysis_period=f"{request.days_back} дней",
            total_messages_analyzed=len(processed_data),
            messages=processed_data,
            analysis_timestamp=start_time.isoformat()
        )
        
    except HTTPException:
        # Переброс HTTP исключений как есть
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
        "main:app",  # Убедитесь, что имя файла main.py
        host="0.0.0.0",
        port=port,
        log_level="info",
        reload=False  # Установите True для разработки
    )
