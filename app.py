"""
🚀 Telegram Channel Analyzer FastAPI with Comments
========================================================
Веб-сервис для анализа Telegram каналов с комментариями из группы обсуждений.
Извлекает статистику сообщений и комментарии к постам.
Автор: AI Assistant
Дата: 2025
"""
import asyncio
import os
import json
import logging
import base64
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Union
from contextlib import asynccontextmanager

# FastAPI и Pydantic импорты
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# Telegram API импорты
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, Message, MessageService
from telethon.errors import SessionPasswordNeededError, FloodWaitError, ChannelPrivateError
from telethon.tl.types import InputPeerChannel, InputMessagesFilterEmpty

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
LIMIT_MESSAGES = int(os.getenv("LIMIT_MESSAGES", "200"))
DAYS_BACK = int(os.getenv("DAYS_BACK", "90"))
SESSION_NAME = "telegram_analyzer_session"
SESSION_FILE = f"{SESSION_NAME}.session"

# Глобальная переменная для хранения клиента
telegram_client: Optional[TelegramClient] = None

# ==============================================================================
# 📋 PYDANTIC МОДЕЛИ ДЛЯ API
# ==============================================================================

class CommentInfo(BaseModel):
    author: Optional[str] = Field(default="Unknown", description="Имя автора комментария")
    date: str = Field(..., description="Дата комментария")
    text: str = Field(..., description="Текст комментария")

class PostInfo(BaseModel):
    date: str
    type: str
    views: int
    comments: int
    reactions: int
    forwards: int
    content: str
    url: str

class PostWithComments(BaseModel):
    post_info: PostInfo
    comments: List[CommentInfo]

class ChannelAnalysisRequest(BaseModel):
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
    include_comments: Optional[bool] = Field(
        default=True,
        description="Включать ли комментарии из группы обсуждений"
    )

    @validator('channel_username')
    def validate_channel_username(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Имя канала не может быть пустым")
        
        if v.startswith('@'):
            v = v[1:]
        
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError("Имя канала содержит недопустимые символы")
            
        return v

class ChannelAnalysisResponse(BaseModel):
    success: bool
    channel_title: str
    channel_username: Optional[str]
    channel_id: int
    subscribers_count: Optional[int]
    discussion_group_id: Optional[int]
    analysis_period: str
    total_messages_analyzed: int
    posts: Dict[str, PostWithComments]
    analysis_timestamp: str

class HealthResponse(BaseModel):
    status: str
    timestamp: str
    telegram_client_status: str
    version: str = "1.0.0"

# ==============================================================================
# 🔐 УПРАВЛЕНИЕ КРЕДЕНЦИАЛАМИ TELEGRAM
# ==============================================================================

def get_telegram_credentials() -> tuple[str, str, str]:
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
            f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}"
        )
    
    logger.info(f"Креденциалы получены для номера: {phone[:4]}****{phone[-4:]}")
    return api_id, api_hash, phone

# ==============================================================================
# 🤖 УПРАВЛЕНИЕ TELEGRAM КЛИЕНТОМ
# ==============================================================================

async def init_telegram_client() -> TelegramClient:
    try:
        api_id, api_hash, phone = get_telegram_credentials()
        
        session_exists = os.path.exists(SESSION_FILE)
        
        if not session_exists:
            session_b64 = os.getenv('TELEGRAM_SESSION_BASE64')
            if session_b64:
                try:
                    session_data = base64.b64decode(session_b64)
                    with open(SESSION_FILE, 'wb') as f:
                        f.write(session_data)
                    logger.info("Файл сессии воссоздан из переменной окружения")
                    session_exists = True
                except Exception as e:
                    logger.error(f"Ошибка декодирования сессии: {e}")
        
        client = TelegramClient(SESSION_NAME, int(api_id), api_hash)
        
        password = os.getenv('TELEGRAM_2FA_PASSWORD')
        if password:
            await client.start(phone, password=password)
        else:
            await client.start(phone)
        
        me = await client.get_me()
        logger.info(f"Успешно авторизован как: {me.first_name} {me.last_name or ''} (ID: {me.id})")
        
        return client
        
    except SessionPasswordNeededError:
        raise Exception(
            "Требуется двухфакторная аутентификация. "
            "Установите переменную окружения TELEGRAM_2FA_PASSWORD."
        )
    except Exception as e:
        logger.error(f"Ошибка инициализации Telegram клиента: {e}")
        if os.path.exists(SESSION_FILE) and ("auth" in str(e).lower() or "invalid" in str(e).lower()):
            try:
                os.remove(SESSION_FILE)
                logger.info("Поврежденный файл сессии удален.")
            except Exception as rm_error:
                logger.warning(f"Не удалось удалить поврежденный файл сессии: {rm_error}")
        raise Exception(f"Не удалось инициализировать Telegram клиент: {str(e)}")

async def get_telegram_client() -> TelegramClient:
    global telegram_client
    
    if telegram_client is None or not telegram_client.is_connected():
        telegram_client = await init_telegram_client()
    
    return telegram_client

# ==============================================================================
# 📊 ОСНОВНЫЕ ФУНКЦИИ АНАЛИЗА
# ==============================================================================

async def find_channel(client: TelegramClient, channel_username: str) -> Channel:
    try:
        original_username = channel_username
        search_username = original_username.lower()
        
        logger.info(f"Ищем канал: {original_username} (поиск: @{search_username})")
        
        try:
            target_channel = await client.get_entity(f"@{search_username}")
        except ValueError:
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
        
        if not isinstance(target_channel, Channel):
            raise HTTPException(
                status_code=400,
                detail=f"@{original_username} не является каналом"
            )
        
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
        raise
    except Exception as e:
        logger.error(f"Ошибка поиска канала @{channel_username}: {e}")
        raise HTTPException(
            status_code=404,
            detail=f"Канал @{channel_username} не найден: {str(e)}"
        )

async def get_channel_info(client: TelegramClient, channel: Channel, original_username: str = None) -> dict:
    display_username = original_username if original_username else getattr(channel, 'username', None)
    
    info = {
        'title': channel.title,
        'username': display_username,
        'id': channel.id,
        'subscribers_count': None,
        'discussion_group_id': None
    }
    
    try:
        full_info = await client(GetFullChannelRequest(channel))
        info['subscribers_count'] = full_info.full_chat.participants_count
        
        if hasattr(full_info.full_chat, 'linked_chat_id') and full_info.full_chat.linked_chat_id:
            info['discussion_group_id'] = full_info.full_chat.linked_chat_id
            logger.info(f"Найдена группа обсуждений: ID {info['discussion_group_id']}")
            
    except Exception as e:
        logger.warning(f"Не удалось получить полную информацию о канале: {e}")
        info['subscribers_count'] = getattr(channel, 'participants_count', None)
    
    return info

async def get_channel_messages(client: TelegramClient, channel: Channel, limit: int, days_back: int) -> list:
    logger.info(f"Загружаем до {limit} последних сообщений")
    
    try:
        all_messages = await client.get_messages(channel, limit=limit)
        logger.info(f"Всего найдено {len(all_messages)} сообщений")
        
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

async def process_comment_message(client: TelegramClient, message) -> Optional[CommentInfo]:
    """Обработка отдельного комментария"""
    try:
        author_name = "Unknown"
        if message.sender_id:
            try:
                sender = await client.get_entity(message.sender_id)
                if hasattr(sender, 'first_name'):
                    author_name = sender.first_name
                    if hasattr(sender, 'last_name') and sender.last_name:
                        author_name += f" {sender.last_name}"
                elif hasattr(sender, 'username') and sender.username:
                    author_name = f"@{sender.username}"
                elif hasattr(sender, 'title'):
                    author_name = sender.title
            except Exception as e:
                logger.debug(f"Не удалось получить информацию об авторе {message.sender_id}: {e}")
        
        msg_date = message.date
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        formatted_date = msg_date.strftime("%Y-%m-%d %H:%M:%S")
        
        # Получаем текст комментария
        text = getattr(message, 'message', '').strip()
        
        # Если текста нет, проверяем наличие медиа
        if not text:
            if hasattr(message, 'media') and message.media:
                media_type = get_media_type(message.media)
                text = f"[{media_type}]"
            else:
                # Пропускаем полностью пустые комментарии
                logger.debug(f"Пропускаем пустой комментарий {message.id}")
                return None
        
        # Проверяем, что текст не состоит только из пробелов или спецсимволов
        if not text or text.isspace() or text in ['[Медиа]', '[Комментарий]']:
            logger.debug(f"Пропускаем неинформативный комментарий: '{text}'")
            return None
        
        return CommentInfo(
            author=author_name,
            date=formatted_date,
            text=text[:500]
        )
        
    except Exception as e:
        logger.warning(f"Ошибка обработки комментария: {e}")
        return None

async def get_all_discussion_messages(client: TelegramClient, discussion_group_id: int) -> List[Any]:
    """
    Получение всех сообщений из группы обсуждений для анализа
    """
    messages = []
    try:
        if not discussion_group_id:
            return messages
            
        discussion_group = await client.get_entity(discussion_group_id)
        logger.info(f"Загружаем сообщения из группы обсуждений {discussion_group_id}")
        
        async for message in client.iter_messages(discussion_group, limit=500):
            if not isinstance(message, MessageService):
                messages.append(message)
                
        logger.info(f"Загружено {len(messages)} сообщений из группы обсуждений")
        
    except Exception as e:
        logger.error(f"Ошибка загрузки сообщений из группы обсуждений: {e}")
    
    return messages

def extract_post_id_from_text(text: str, channel_id: int) -> Optional[int]:
    """
    Извлечение ID поста из текста комментария
    """
    if not text:
        return None
    
    # Паттерны для поиска ссылок на посты
    patterns = [
        rf't\.me/c/{channel_id}/(\d+)',
        rf't\.me/\w+/(\d+)',
        rf'/{channel_id}/(\d+)',
        rf'#(\d+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, TypeError):
                continue
    
    return None

async def find_comments_for_post(client: TelegramClient, discussion_messages: List[Any], post_id: int, channel_id: int) -> List[CommentInfo]:
    """
    Поиск комментариев для конкретного поста
    """
    comments = []
    processed_message_ids = set()
    
    for message in discussion_messages:
        try:
            message_id = getattr(message, 'id', None)
            if message_id in processed_message_ids:
                continue
                
            processed_message_ids.add(message_id)
            
            comment = None
            
            # Метод 1: Проверка reply_to_msg_id
            if (hasattr(message, 'reply_to') and 
                hasattr(message.reply_to, 'reply_to_msg_id') and
                message.reply_to.reply_to_msg_id == post_id):
                
                comment = await process_comment_message(client, message)
            
            # Метод 2: Проверка пересланных сообщений
            elif (hasattr(message, 'fwd_from') and
                  hasattr(message.fwd_from, 'channel_id') and
                  message.fwd_from.channel_id == channel_id and
                  hasattr(message.fwd_from, 'channel_post') and
                  message.fwd_from.channel_post == post_id):
                
                comment = await process_comment_message(client, message)
            
            # Метод 3: Поиск по тексту (ссылкам на пост)
            elif hasattr(message, 'message') and message.message:
                text = message.message
                extracted_post_id = extract_post_id_from_text(text, channel_id)
                if extracted_post_id == post_id:
                    comment = await process_comment_message(client, message)
            
            # Добавляем комментарий, если он валидный
            if comment:
                comments.append(comment)
                logger.debug(f"Найден комментарий для поста {post_id}: {comment.text[:50]}...")
                
        except Exception as e:
            logger.warning(f"Ошибка обработки сообщения {getattr(message, 'id', 'unknown')}: {e}")
            continue
    
    return comments

async def get_post_comments(client: TelegramClient, discussion_group_id: int, post_id: int, channel_id: int, discussion_messages: List[Any]) -> List[CommentInfo]:
    """
    Получение комментариев к посту из группы обсуждений
    """
    comments = []
    
    try:
        if not discussion_group_id:
            return comments
        
        logger.info(f"Ищем комментарии для поста {post_id}")
        
        # Используем предзагруженные сообщения для поиска
        comments = await find_comments_for_post(client, discussion_messages, post_id, channel_id)
        
        # Логируем детали найденных комментариев
        for i, comment in enumerate(comments, 1):
            logger.debug(f"Комментарий {i} к посту {post_id}: {comment.text[:50]}...")
        
        logger.info(f"Найдено {len(comments)} комментариев к посту {post_id}")
        
    except Exception as e:
        logger.error(f"Ошибка получения комментариев для поста {post_id}: {e}")
    
    return comments

def get_media_type(media) -> str:
    if media is None:
        return "Текст"
    
    media_type = str(type(media).__name__)
    
    if 'Photo' in media_type:
        return "Фото"
    elif 'Video' in media_type:
        return "Видео"
    elif 'Document' in media_type:
        return "Документ"
    elif 'Audio' in media_type:
        return "Аудио"
    elif 'Sticker' in media_type:
        return "Стикер"
    elif 'Poll' in media_type:
        return "Опрос"
    elif 'WebPage' in media_type:
        return "Веб-страница"
    elif 'Game' in media_type:
        return "Игра"
    else:
        return "Медиа"

async def process_channel_posts_with_comments(
    client: TelegramClient, 
    messages: list, 
    channel: Channel,
    discussion_group_id: Optional[int],
    include_comments: bool = True
) -> dict:
    posts_data = {}
    processed_count = 0
    
    logger.info(f"Начинаем обработку {len(messages)} постов с комментариями: {include_comments}")
    
    # Предзагружаем все сообщения из группы обсуждений один раз
    discussion_messages = []
    if include_comments and discussion_group_id:
        discussion_messages = await get_all_discussion_messages(client, discussion_group_id)
        logger.info(f"Загружено {len(discussion_messages)} сообщений для анализа комментариев")
    
    for i, msg in enumerate(reversed(messages), 1):
        try:
            if isinstance(msg, MessageService):
                continue
            
            logger.info(f"Обрабатываем пост {i}: ID={msg.id}, Дата={msg.date}")
            
            post_type = "Текст"
            content = ""
            
            if hasattr(msg, 'message') and msg.message:
                content = msg.message.strip()
            
            if hasattr(msg, 'media') and msg.media:
                media_type = get_media_type(msg.media)
                post_type = media_type
                if content:
                    content = f"[{media_type}] {content}"
                else:
                    content = f"[{media_type}]"
            
            if not content:
                content = "[Пустой пост]"
            
            views_count = getattr(msg, 'views', 0) or 0
            
            reactions_count = 0
            try:
                if hasattr(msg, 'reactions') and msg.reactions and hasattr(msg.reactions, 'results'):
                    if msg.reactions.results:
                        reactions_count = sum([r.count for r in msg.reactions.results if hasattr(r, 'count') and r.count])
            except Exception as e:
                logger.warning(f"Ошибка получения реакций для поста {msg.id}: {e}")
            
            forwards_count = getattr(msg, 'forwards', 0) or 0
            
            msg_date = msg.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            formatted_date = msg_date.strftime("%Y-%m-%d %H:%M:%S")
            
            channel_username = getattr(channel, 'username', None)
            post_link = ""
            if channel_username:
                post_link = f"https://t.me/{channel_username}/{msg.id}"
            else:
                post_link = f"Пост #{msg.id}"
            
            comments_count = 0
            comments_list = []
            
            if include_comments and discussion_group_id and discussion_messages:
                comments_list = await get_post_comments(client, discussion_group_id, msg.id, channel.id, discussion_messages)
                comments_count = len(comments_list)
                
                # Детальное логирование комментариев
                if comments_list:
                    for j, comment in enumerate(comments_list, 1):
                        logger.info(f"Пост {msg.id}, комментарий {j}: '{comment.text}'")
                else:
                    logger.info(f"Для поста {msg.id} комментарии не найдены")
            
            post_info = PostInfo(
                date=formatted_date,
                type=post_type,
                views=views_count,
                comments=comments_count,
                reactions=reactions_count,
                forwards=forwards_count,
                content=content[:1000],
                url=post_link
            )
            
            posts_data[str(msg.id)] = PostWithComments(
                post_info=post_info,
                comments=comments_list
            )
            
            processed_count += 1
            logger.info(f"Пост {msg.id} успешно обработан с {comments_count} комментариями")
            
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
    logger.info("🚀 Запуск Telegram Channel Analyzer API с комментариями")
    
    try:
        global telegram_client
        telegram_client = await init_telegram_client()
        logger.info("✅ Telegram клиент успешно инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Telegram клиента: {e}")
    
    yield
    
    if telegram_client:
        await telegram_client.disconnect()
        logger.info("🔐 Telegram клиент отключен")

# ==============================================================================
# 🌍 СОЗДАНИЕ FASTAPI ПРИЛОЖЕНИЯ
# ==============================================================================

app = FastAPI(
    title="Telegram Channel Analyzer with Comments",
    description="🤖 API для анализа статистики Telegram каналов с комментариями",
    version="1.3.0",
    lifespan=lifespan
)

# ==============================================================================
# 📍 API ЭНДПОИНТЫ
# ==============================================================================

@app.get("/", response_model=HealthResponse)
async def root():
    return HealthResponse(
        status="running",
        timestamp=datetime.now(timezone.utc).isoformat(),
        telegram_client_status="connected" if telegram_client and telegram_client.is_connected() else "disconnected"
    )

@app.get("/health", response_model=HealthResponse)
async def health_check():
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
    start_time = datetime.now(timezone.utc)
    logger.info(f"Начинаем анализ канала: @{request.channel_username} с комментариями: {request.include_comments}")
    
    try:
        client = await get_telegram_client()
        channel = await find_channel(client, request.channel_username)
        channel_info = await get_channel_info(client, channel, request.channel_username)
        
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
                discussion_group_id=channel_info['discussion_group_id'],
                analysis_period=f"{request.days_back} дней",
                total_messages_analyzed=0,
                posts={},
                analysis_timestamp=start_time.isoformat()
            )
        
        posts_data = await process_channel_posts_with_comments(
            client,
            messages, 
            channel,
            channel_info['discussion_group_id'],
            request.include_comments
        )
        
        end_time = datetime.now(timezone.utc)
        processing_time = (end_time - start_time).total_seconds()
        
        logger.info(f"Анализ завершен за {processing_time:.2f} секунд. Обработано {len(posts_data)} постов")
        
        return ChannelAnalysisResponse(
            success=True,
            channel_title=channel_info['title'],
            channel_username=channel_info['username'],
            channel_id=channel_info['id'],
            subscribers_count=channel_info['subscribers_count'],
            discussion_group_id=channel_info['discussion_group_id'],
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
            "service": "Telegram Channel Analyzer with Comments",
            "version": "1.3.0",
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
