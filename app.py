# Замените функцию get_channel_messages на следующую:

async def get_channel_messages(client: TelegramClient, channel: Channel, limit: int, days_back: int) -> list:
    """
    Получение основных постов канала
    
    Args:
        client: Telegram клиент
        channel: Канал
        limit: Лимит сообщений
        days_back: Дней назад для поиска
        
    Returns:
        list: Список основных постов канала
    """
    offset_date = datetime.now(timezone.utc) - timedelta(days=days_back)
    
    logger.info(f"Загружаем до {limit} сообщений за последние {days_back} дней")
    
    try:
        # Получаем сообщения из канала
        all_messages = await client.get_messages(channel, limit=limit, offset_date=offset_date)
        logger.info(f"Всего найдено {len(all_messages)} сообщений")
        
        # Фильтруем только основные посты канала
        channel_posts = []
        for msg in all_messages:
            msg_date = msg.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            
            # Упрощенная проверка что это пост канала в нужном периоде
            is_channel_post = (
                msg_date >= offset_date and          # В нужном периоде
                msg.reply_to_msg_id is None and      # Не является ответом
                not msg.replies_thread_id and        # Не является частью темы обсуждения
                (msg.post or msg.from_id is None)    # Является постом канала
            )
            
            if is_channel_post:
                channel_posts.append(msg)
        
        logger.info(f"После фильтрации основных постов: {len(channel_posts)} сообщений")
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

# Замените соответствующую часть в функции process_channel_posts:

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
            # Определяем тип поста и содержание
            post_type = "Текст"
            content = ""
            
            if hasattr(msg, 'message') and msg.message:
                content = msg.message
                if hasattr(msg, 'media') and msg.media:
                    post_type = get_media_type(msg.media)
                    content = f"[{post_type}] {content}"
            elif hasattr(msg, 'media') and msg.media:
                post_type = get_media_type(msg.media)
                content = f"[{post_type}]"
            else:
                # Даже если нет текста, это может быть пост
                post_type = "Текст"
                content = "[Пустой пост]" if not (hasattr(msg, 'media') and msg.media) else ""
                if hasattr(msg, 'media') and msg.media:
                    post_type = get_media_type(msg.media)
                    content = f"[{post_type}]"
            
            # Получаем статистику поста
            views_count = getattr(msg, 'views', 0) or 0
            
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
            
            # Создаем ссылку на пост (исправлен URL)
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
                'content': content[:1000],  # Ограничиваем длину контента
                'url': post_link
            }
            
            processed_count += 1
            
            # Логируем прогресс каждые 10 постов
            if i % 10 == 0:
                logger.info(f"Обработано {i}/{len(messages)} постов")
                
        except Exception as e:
            logger.error(f"Ошибка обработки поста {msg.id}: {e}")
            continue
    
    logger.info(f"Обработано постов: {processed_count}")
    return posts_data
