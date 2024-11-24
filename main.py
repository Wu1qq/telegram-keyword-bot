from telegram.ext import Application, CommandHandler, MessageHandler, filters
from config import Config
from logger import setup_logger
import asyncio
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple, Callable, Set
import yaml
import tempfile
import os
from functools import wraps
from time import time
import psutil
from cryptography.fernet import Fernet
import json
import shutil
from enum import Enum
from models import init_db, User, Subscription, MessageQueue, HealthCheck
from sqlalchemy.orm import Session
from queue import Queue
import threading

logger = setup_logger()

class Subscription:
    def __init__(self, keyword: str, is_regex: bool = False, filters: dict = None):
        self.keyword = keyword
        self.is_regex = is_regex
        self.regex_pattern = re.compile(keyword) if is_regex else None
        self.filters = filters or {}
        
        # 解析时间段
        if 'time_range' in self.filters:
            start, end = self.filters['time_range']
            self.start_time = datetime.strptime(start, '%H:%M').time()
            self.end_time = datetime.strptime(end, '%H:%M').time()
        
        # 添加新的过滤选项
        if 'min_length' in self.filters:
            self.filters['min_length'] = int(self.filters['min_length'])
        if 'max_length' in self.filters:
            self.filters['max_length'] = int(self.filters['max_length'])
        if 'exclude_keywords' in self.filters:
            self.filters['exclude_keywords'] = set(self.filters['exclude_keywords'])
        
        self.enabled = True  # 添加启用/禁用状态
        self.template = None  # 自定义通知模板
        self.priority = filters.get('priority', 0)  # 消息优先级
        self.aggregate = filters.get('aggregate', False)  # 是否聚合消息
        self.aggregate_interval = filters.get('aggregate_interval', 300)  # 聚合时间间隔(秒)
        self.tags = filters.get('tags', [])  # 订阅标签
        self.note = filters.get('note', '')  # 订阅备注
        self.forward_to = filters.get('forward_to', [])  # 转发目标

    def match(self, text: str, message) -> bool:
        # 首先检查关键词匹配
        if self.is_regex:
            if not self.regex_pattern.search(text):
                return False
        elif self.keyword not in text:
            return False
            
        # 检查消息类型过滤
        if 'message_types' in self.filters:
            message_type = self._get_message_type(message)
            if message_type not in self.filters['message_types']:
                return False
                
        # 检查发送者过滤
        if 'sender_types' in self.filters:
            sender_type = self._get_sender_type(message)
            if sender_type not in self.filters['sender_types']:
                return False
                
        # 检查时间段过滤
        if 'time_range' in self.filters:
            current_time = datetime.now().time()
            if not (self.start_time <= current_time <= self.end_time):
                return False
                
        # 检查群组/频道过滤
        if 'chat_ids' in self.filters:
            if message.chat.id not in self.filters['chat_ids']:
                return False
                
        # 添加消息长度过滤
        if 'min_length' in self.filters and len(text) < self.filters['min_length']:
            return False
        if 'max_length' in self.filters and len(text) > self.filters['max_length']:
            return False
            
        # 添加排除关键词过滤
        if 'exclude_keywords' in self.filters:
            for exclude_word in self.filters['exclude_keywords']:
                if exclude_word in text:
                    return False
                    
        # 添加转发消息过滤
        if 'allow_forwarded' in self.filters:
            if not self.filters['allow_forwarded'] and message.forward_date:
                return False
                
        # 添加媒体标题过滤
        if 'check_media_caption' in self.filters:
            if not self.filters['check_media_caption']:
                if not message.text:  # 如果是媒体消息的标题就跳
                    return False
                    
        return True
        
    def _get_message_type(self, message):
        if message.text:
            return 'text'
        elif message.photo:
            return 'photo'
        elif message.video:
            return 'video'
        elif message.document:
            return 'document'
        elif message.voice:
            return 'voice'
        return 'other'
        
    def _get_sender_type(self, message):
        if message.from_user:
            if message.chat.get_member(message.from_user.id).status in ['creator', 'administrator']:
                return 'admin'
            return 'user'
        return 'anonymous'

def error_handler(func: Callable):
    """错误处理装饰器"""
    @wraps(func)
    async def wrapper(self, update, context, *args, **kwargs):
        try:
            return await func(self, update, context, *args, **kwargs)
        except Exception as e:
            logger.error(f"执行命令 {func.__name__} 时发生错误: {str(e)}")
            await update.message.reply_text(
                f"执行命令时发生错误：{str(e)}\n"
                f"如果问题持续存在，请联系管理员。"
            )
    return wrapper

def rate_limit(limit: int, window: int = 60):
    """命令限流装饰器"""
    def decorator(func: Callable):
        command_limits: Dict[int, list] = {}
        
        @wraps(func)
        async def wrapper(self, update, context, *args, **kwargs):
            user_id = update.effective_user.id
            current_time = time()
            
            # 初始化用户的命令记录
            if user_id not in command_limits:
                command_limits[user_id] = []
                
            # 清理过期的命令记录
            command_limits[user_id] = [
                t for t in command_limits[user_id]
                if current_time - t < window
            ]
            
            # 检查是否超过限制
            if len(command_limits[user_id]) >= limit:
                await update.message.reply_text(
                    f"您的操作太频繁了，请等待 {window} 秒后再试。"
                )
                return
                
            # 记录本次命令
            command_limits[user_id].append(current_time)
            return await func(self, update, context, *args, **kwargs)
        return wrapper
    return decorator

def admin_required(func: Callable):
    """管理员权限检查装饰器"""
    @wraps(func)
    async def wrapper(self, update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in self.config.admin_users:
            await update.message.reply_text("此命令仅管理员可用")
            return
        return await func(self, update, context, *args, **kwargs)
    return wrapper

class CommandLevel(Enum):
    """命令权限等级"""
    USER = 0      # 普通用户
    PREMIUM = 1   # 高级用户
    ADMIN = 2     # 管理员
    OWNER = 3     # 所有者

class Session:
    """用户会话类"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.state = None  # 当前状态
        self.data = {}    # 会话数据
        self.last_activity = datetime.now()
        self.command_level = CommandLevel.USER
        
    def update_activity(self):
        """更新最后活动时间"""
        self.last_activity = datetime.now()

class FilterCombination:
    """过滤器组合类"""
    def __init__(self, name: str, filters: list, operator: str = 'AND'):
        self.name = name
        self.filters = filters  # 过滤器列表
        self.operator = operator  # 组合操作符：AND/OR
        
    def match(self, text: str, message) -> bool:
        if self.operator == 'AND':
            return all(f.match(text, message) for f in self.filters)
        return any(f.match(text, message) for f in self.filters)

class KeywordBot:
    def __init__(self):
        self.config = Config()
        # 使用代理配置创建 Application
        kwargs = {}
        if self.config.proxy:
            kwargs['proxy_url'] = (
                f"{self.config.proxy['scheme']}://"
                f"{self.config.proxy['hostname']}:{self.config.proxy['port']}"
            )
            if self.config.proxy['username'] and self.config.proxy['password']:
                kwargs['proxy_auth'] = (
                    self.config.proxy['username'],
                    self.config.proxy['password']
                )
        
        self.app = Application.builder().token(self.config.bot_token).build(**kwargs)
        # 存储格式: {user_id: [Subscription对象]}
        self.subscriptions: Dict[int, List[Subscription]] = {}
        # 存储用户监控的频道: {user_id: [channel_id]}
        self.monitored_channels: Dict[int, List[int]] = {}
        self.blacklist: Dict[int, List[int]] = {}  # 用户的黑名单 {user_id: [blocked_id]}
        self.stats = {}  # 用户统计信息 {user_id: {'matches': 0, 'last_match': None}}
        self.subscription_groups = {}  # 订阅分组 {user_id: {'group_name': [subscriptions]}}
        self.default_template = self.config.notification['template']
        self.message_history = {}  # 用于存储最近的消息 {user_id: [(message_hash, timestamp)]}
        self.delayed_messages = {}  # 用于存储延迟发送的消息 {user_id: [(message, send_time)]}
        self.aggregated_messages = {}  # 存储待聚合的消息 {user_id: {keyword: [messages]}}
        self.last_cleanup = datetime.now()  # 上次清理时间
        self.tags = {}  # 用户的标签集合 {user_id: set(tags)}
        
        # 添加验证状态
        self.verified = False
        
        # 设置验证处理
        self.app.bot.set_my_commands([
            ("start", "开始使用机器人"),
            ("help", "查看帮助信息"),
            # ... (其他命令)
        ])
        
        self.filter_rules = {}  # 用户的过滤规则 {user_id: [rules]}
        self.scheduled_tasks = {}  # 定时任务 {user_id: [tasks]}
        self.message_stats = {}  # 消息统计 {user_id: {'total': 0, 'matched': 0, 'types': {}}}
        self.user_permissions: Dict[int, Set[str]] = {}  # 用户权限集合
        self.reconnect_attempts = 0  # 重连次数
        self.max_reconnect_attempts = 5  # 最大重连次数
        self.reconnect_delay = 5  # 重连延迟（秒）
        self.cache_size = 1000  # 缓存大小限制
        self.performance_stats = {
            'message_processing_time': [],  # 消息处理时间统计
            'memory_usage': [],  # 内存使用统计
            'start_time': datetime.now()  # 启动时间
        }
        
        # 加密相关
        self.encryption_key = Fernet.generate_key()
        self.cipher_suite = Fernet(self.encryption_key)
        
        # 用户配额
        self.user_quotas = {}  # {user_id: {'daily': count, 'last_reset': datetime}}
        
        # 备份设置
        self.backup_interval = timedelta(days=1)  # 默认每天备份
        self.last_backup = datetime.now()
        self.max_backups = 7  # 保留最近7天的备份
        
        # 添加新的属性
        self.sessions: Dict[int, Session] = {}  # 用户会话
        self.command_aliases = {}  # 命令别名 {alias: original_command}
        self.command_levels = {    # 命令权限等级设置
            "broadcast": CommandLevel.ADMIN,
            "set_user_permission": CommandLevel.ADMIN,
            "performance_stats": CommandLevel.ADMIN,
            # ... 其他命令的权限设置
        }
        
        # 添加新的属性
        self.forward_limits = {}  # 转发限制 {user_id: {'daily': limit, 'used': count}}
        self.custom_shortcuts = {}  # 自定义快捷键 {user_id: {'shortcut': 'command'}}
        self.filter_combinations = {}  # 过滤器组合 {user_id: {'name': FilterCombination}}
        
        # 添加新的属性
        self.filter_templates = {}  # 过滤器模板 {user_id: {'template_name': filters}}
        self.user_logs = {}  # 用户操作日志 {user_id: [{'action': action, 'time': time, 'details': details}]}
        self.cleanup_settings = {
            'message_history_days': 7,  # 消息历史保留天数
            'user_logs_days': 30,       # 用户日志保留天数
            'backup_interval_days': 1,   # 备份间隔天数
            'backup_retention_days': 7   # 备份保留天数
        }
        
        # 初始化数据库
        self.db: Session = init_db(self.config.database_url)
        
        # 初始化消息队列
        self.message_queue = Queue()
        self.queue_worker = threading.Thread(target=self._process_message_queue)
        self.queue_worker.daemon = True
        self.queue_worker.start()
        
    async def start(self, update, context):
        await update.message.reply_text(
            "欢迎使用关键词提醒机器人！\n"
            "使用 /help 查看帮助信息"
        )
    
    async def help(self, update, context):
        help_text = """
可用命令：
/订阅 <关键词> [过滤选项] - 添加新的关键词订阅
/订阅正则 <正则表达式> [过滤选项] - 添加正则表达式订阅
/取消订阅 <关键词> - 删除关键词订阅
/我的订阅 - 查看当前订阅列表
/添加频道 <频道ID> - 添加私有频道监控
/删除频道 <频道ID> - 删除私有频道监控
/频道列表 - 查看监控的频道列表
/帮助 - 显示本帮助信息
/黑名单 <用户ID> - 将用户加入黑名单
/移除黑名单 <用户ID> - 将用户从黑名单移除
/黑名单列表 - 查看黑名单列表
/设置预览 <行数> - 设置消息预览的上下文行数
/统计 - 显示用户的统计信息
/创建分组 <分组名称> - 创建新的订阅分组
/添加到分组 <分组名称> <关键词> - 将关键词添加到指定分组
/分组列表 - 查看所有分组及其订阅
/导出配置 - 导出用户的订阅配置
/导入配置 - 导入订阅配置
/设置模板 <关键词> <模板> - 设置订阅的自定义通知模板
/开关订阅 <关键词> on/off - 启用/禁用订阅
/批量订阅 - 批量添加订阅
/设置延迟 <延迟时间> - 设置消息延迟发送时间
/设置格式 <格式化选项> - 设置消息格式化选项
/设置优先级 <关键词> <优先级> - 设置订阅的优先级
/设置聚合 <关键词> <是否聚合> <时间间隔> - 设置消息聚合

过滤选项：
--type=类型1,类型2    - 按消息类型过滤(text,photo,video,document,voice)
--sender=类型1,类型2   - 按发送者过滤(user,admin,anonymous)
--time=HH:MM-HH:MM    - 按时间段过滤
--chat=ID1,ID2        - 按群组/频道过滤
--min=数字            - 最小消息长度
--max=数字            - 最大消息长度
--exclude=词1,词2     - 排除包含指定关键词的消息
--forward=yes/no      - 是否包含转发消息
--media=yes/no        - 是否检查媒体消息标题
--link=yes/no        - 是否包含消息链接
--quote=yes/no       - 是否包含引用消息
--context=数字       - 显示匹配消息前后的行数

示例：
/订阅 测试 --type=text,photo --time=9:00-18:00
/订阅正则 \\d{11} --sender=user --min=10 --exclude=广告,推广
/订阅 优惠券 --forward=no --media=yes --max=100
/订阅 通知 --link=yes --quote=yes --context=2
/设置模板 测试 {keyword} 在 {group_name} 被提及
/开关订阅 测试 off
/批量订阅
关键词1 --type=text
关键词2 --sender=admin
关键词3 --time=9:00-18:00

使用说明：
1. 关键词支持普通文本和正则表达式
2. 每个用户最多可订阅 {max_keywords} 个关键词
3. 将机器人添加到群组或频道中即可开始监控
4. 正则表达式示例：\\b\\d{11}\\b（匹配11位手机号）
        """.format(max_keywords=self.config.monitor['max_keywords_per_user'])
        await update.message.reply_text(help_text)
    
    @error_handler
    @rate_limit(5)  # 每60秒最多5次
    async def subscribe(self, update, context):
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text(
                "请指定要订阅的关键词和过滤选项，例如：\n"
                "/订阅 测试 --type=text,photo --time=9:00-18:00"
            )
            return
            
        # 解析命令参数
        keyword = context.args[0]
        filters = {}
        
        for arg in context.args[1:]:
            if arg.startswith('--'):
                try:
                    key, value = arg[2:].split('=')
                    if key == 'type':
                        filters['message_types'] = value.split(',')
                    elif key == 'sender':
                        filters['sender_types'] = value.split(',')
                    elif key == 'time':
                        start, end = value.split('-')
                        filters['time_range'] = (start, end)
                    elif key == 'chat':
                        filters['chat_ids'] = [int(x) for x in value.split(',')]
                    elif key == 'min':
                        filters['min_length'] = int(value)
                    elif key == 'max':
                        filters['max_length'] = int(value)
                    elif key == 'exclude':
                        filters['exclude_keywords'] = value.split(',')
                    elif key == 'forward':
                        filters['allow_forwarded'] = value.lower() == 'yes'
                    elif key == 'media':
                        filters['check_media_caption'] = value.lower() == 'yes'
                    elif key == 'link':
                        filters['link'] = value.lower() == 'yes'
                    elif key == 'quote':
                        filters['quote'] = value.lower() == 'yes'
                    elif key == 'context':
                        filters['context'] = int(value)
                except ValueError:
                    results.append(f"❌ {keyword}: 无效的过滤选项 {part}")
                    continue
            
            try:
                # 检查订阅限制
                if len(self.subscriptions.get(user_id, [])) >= self.config.monitor['max_keywords_per_user']:
                    results.append(f"❌ {keyword}: 已达到最大订阅数量限制")
                    continue
            
        # 添加订阅
        if user_id not in self.subscriptions:
            self.subscriptions[user_id] = []
                self.subscriptions[user_id].append(Subscription(keyword, False, filters))
                results.append(f"✅ {keyword}: 订阅成功")
            except Exception as e:
                results.append(f"❌ {keyword}: {str(e)}")
                
        await update.message.reply_text("\n".join(results))
    
    async def add_channel(self, update, context):
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请指定要监控的频道ID，例如：/添加频道 -1001234567890")
            return
            
        try:
            channel_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("无效的频道ID")
            return
            
        # 验证机器人是否在频道中
        try:
            chat = await context.bot.get_chat(channel_id)
            member = await chat.get_member(context.bot.id)
            if not member.can_read_messages:
                await update.message.reply_text("机器人没有该频道的读取权限")
                return
        except Exception as e:
            await update.message.reply_text(f"无法访问该频道：{str(e)}")
            return
            
        if user_id not in self.monitored_channels:
            self.monitored_channels[user_id] = []
        if channel_id not in self.monitored_channels[user_id]:
            self.monitored_channels[user_id].append(channel_id)
            
        await update.message.reply_text(f"成功添加频道监控：{chat.title}")
    
    async def remove_channel(self, update, context):
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请指定要删除的频道ID")
            return
            
        try:
            channel_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("无效的频道ID")
            return
            
        if (user_id in self.monitored_channels and 
            channel_id in self.monitored_channels[user_id]):
            self.monitored_channels[user_id].remove(channel_id)
            await update.message.reply_text("已删除该频道的监控")
        else:
            await update.message.reply_text("未找到该频道的监控")
    
    async def list_channels(self, update, context):
        user_id = update.effective_user.id
        if user_id not in self.monitored_channels or not self.monitored_channels[user_id]:
            await update.message.reply_text("您当前没有监控任何频道")
            return
            
        channel_list = []
        for channel_id in self.monitored_channels[user_id]:
            try:
                chat = await context.bot.get_chat(channel_id)
                channel_list.append(f"ID: {channel_id}\n标题: {chat.title}")
            except Exception:
                channel_list.append(f"ID: {channel_id}\n标题: 无法访问")
                
        await update.message.reply_text(
            "您监控的频道列表：\n\n" + "\n\n".join(channel_list)
        )
    
    async def handle_message(self, update, context):
        """处理消息，检查是否匹配订阅关键词"""
        if not update.message or not update.message.text:
            return
            
        message = update.message
        text = message.text or message.caption or ""
        chat_id = message.chat.id
        
        # 获取消息上下文（如果是群组消息）
        context_messages = []
        if message.chat.type in ['group', 'supergroup']:
            try:
                # 获取消息前后的消息
                async for msg in message.chat.iter_messages(
                    limit=5,  # 默认获取前后各2条消息
                    offset_id=message.message_id
                ):
                    context_messages.append(msg)
            except Exception as e:
                logger.error(f"获取消息上下文失败: {str(e)}")
        
        # 按优先级排序订阅
        sorted_subscriptions = sorted(
            self.subscriptions.items(),
            key=lambda x: max((sub.priority for sub in x[1]), default=0),
            reverse=True
        )
        
        for user_id, subs in sorted_subscriptions:
            # 检查黑名单
            if (user_id in self.blacklist and 
                getattr(message.from_user, 'id', None) in self.blacklist[user_id]):
                continue
                
            # 检查是否是用户监控的频道
            if (message.chat.type == "channel" and 
                user_id in self.monitored_channels and 
                chat_id not in self.monitored_channels[user_id]):
                continue
                
            for sub in subs:
                # 检查订阅是否启用
                if not sub.enabled:
                    continue
                    
                if sub.match(text, message):
                    # 处理消息聚合
                    if sub.filters.get('aggregate'):
                        if user_id not in self.aggregated_messages:
                            self.aggregated_messages[user_id] = {}
                        if sub.keyword not in self.aggregated_messages[user_id]:
                            self.aggregated_messages[user_id][sub.keyword] = []
                            
                        # 添加消息到聚合队列
                        self.aggregated_messages[user_id][sub.keyword].append({
                            'text': text,
                            'group_name': message.chat.title,
                            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        
                        # 检查是否需要发送聚合消息
                        if len(self.aggregated_messages[user_id][sub.keyword]) >= 5:
                            await self.send_aggregated_messages(user_id, sub.keyword)
                        continue
                    
                    # 检查是否是重复消息
                    if self.is_duplicate_message(user_id, text):
                        continue
                        
                    # 更新统计信息
                    if user_id not in self.stats:
                        self.stats[user_id] = {'matches': 0, 'last_match': None}
                    self.stats[user_id]['matches'] += 1
                    self.stats[user_id]['last_match'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    # 使用自定义模板或默认模板
                    template = sub.template or self.default_template
                    notification_parts = []
                    
                    # 应用格式化选项
                    if 'format' in sub.filters:
                        text = self.format_message(text, sub.filters['format'])
                        
                    notification = template.format(
                        keyword=sub.keyword,
                        group_name=message.chat.title,
                        sender_id=getattr(message.from_user, 'id', 'N/A'),
                        sender_username=getattr(message.from_user, 'username', 'N/A'),
                        sender_name=getattr(message.from_user, 'full_name', 'N/A'),
                        source="频道消息" if message.chat.type == "channel" else "群组消息"
                    )
                    notification_parts.append(notification)
                    
                    # 添加消息链接（如果可用）
                    if sub.filters.get('link', True) and message.link:
                        notification_parts.append(f"\n消息链接：{message.link}")
                    
                    # 添加引用消息（如果有）
                    if sub.filters.get('quote', True) and message.reply_to_message:
                        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
                        if reply_text:
                            notification_parts.append(f"\n引用消息：\n{reply_text}")
                    
                    # 添加消息上下文（如果需要）
                    if context_messages and sub.filters.get('context', 0) > 0:
                        context_text = "\n消息上下文：\n"
                        for ctx_msg in context_messages:
                            if ctx_msg.text:
                                context_text += f"{ctx_msg.from_user.first_name}: {ctx_msg.text}\n"
                        notification_parts.append(context_text)
                    
                    # 处理延迟发送
                    if 'delay' in sub.filters and sub.filters['delay'] > 0:
                        send_time = datetime.now().timestamp() + sub.filters['delay']
                        if user_id not in self.delayed_messages:
                            self.delayed_messages[user_id] = []
                        self.delayed_messages[user_id].append((
                            (context.bot, {
                                'text': '\n'.join(notification_parts),
                                'disable_web_page_preview': True
                            }),
                            send_time
                        ))
                    else:
                        try:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text='\n'.join(notification_parts),
                                disable_web_page_preview=True
                            )
                        except Exception as e:
                            logger.error(f"发送通知失败: {str(e)}")
                    
                    # 处理消息转发
                    if 'forward_to' in sub.filters:
                        for target in sub.filters['forward_to']:
                            try:
                                await message.forward(target)
                            except Exception as e:
                                logger.error(f"转发消息失败: {str(e)}")
                                
            # 更新统计信息
            message_type = self._get_message_type(message)
            matched = False
            
            for user_id, subs in sorted_subscriptions:
                # 检查黑名单
                if (user_id in self.blacklist and 
                    getattr(message.from_user, 'id', None) in self.blacklist[user_id]):
                    continue
                    
                # 检查是否是用户监控的频道
                if (message.chat.type == "channel" and 
                    user_id in self.monitored_channels and 
                    chat_id not in self.monitored_channels[user_id]):
                    continue
                    
                for sub in subs:
                    if sub.match(text, message):
                        matched = True
                        # 处理消息聚合
                        if sub.filters.get('aggregate'):
                            if user_id not in self.aggregated_messages:
                                self.aggregated_messages[user_id] = {}
                            if sub.keyword not in self.aggregated_messages[user_id]:
                                self.aggregated_messages[user_id][sub.keyword] = []
                                
                            # 添加消息到聚合队列
                            self.aggregated_messages[user_id][sub.keyword].append({
                                'text': text,
                                'group_name': message.chat.title,
                                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            })
                                
                            # 检查是否需要发送聚合消息
                            if len(self.aggregated_messages[user_id][sub.keyword]) >= 5:
                                await self.send_aggregated_messages(user_id, sub.keyword)
                            continue
                        
                        # 检查是否是重复消息
                        if self.is_duplicate_message(user_id, text):
                            continue
                            
                        # 更新统计信息
                        if user_id not in self.stats:
                            self.stats[user_id] = {'matches': 0, 'last_match': None}
                        self.stats[user_id]['matches'] += 1
                        self.stats[user_id]['last_match'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            
                        # 使用自定义模板或默认模板
                        template = sub.template or self.default_template
                        notification_parts = []
                            
                        # 应用格式化选项
                        if 'format' in sub.filters:
                            text = self.format_message(text, sub.filters['format'])
                            
                        notification = template.format(
                            keyword=sub.keyword,
                            group_name=message.chat.title,
                            sender_id=getattr(message.from_user, 'id', 'N/A'),
                            sender_username=getattr(message.from_user, 'username', 'N/A'),
                            sender_name=getattr(message.from_user, 'full_name', 'N/A'),
                            source="频道消息" if message.chat.type == "channel" else "群组消息"
                        )
                        notification_parts.append(notification)
                            
                        # 添加消息链接（如果可用）
                        if sub.filters.get('link', True) and message.link:
                            notification_parts.append(f"\n消息链接：{message.link}")
                            
                        # 添加引用消息（如果有）
                        if sub.filters.get('quote', True) and message.reply_to_message:
                            reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
                            if reply_text:
                                notification_parts.append(f"\n引用消息：\n{reply_text}")
                            
                        # 添加消息上下文（如果需要）
                        if context_messages and sub.filters.get('context', 0) > 0:
                            context_text = "\n消息上下文：\n"
                            for ctx_msg in context_messages:
                                if ctx_msg.text:
                                    context_text += f"{ctx_msg.from_user.first_name}: {ctx_msg.text}\n"
                            notification_parts.append(context_text)
                            
                        # 处理延迟发送
                        if 'delay' in sub.filters and sub.filters['delay'] > 0:
                            send_time = datetime.now().timestamp() + sub.filters['delay']
                            if user_id not in self.delayed_messages:
                                self.delayed_messages[user_id] = []
                            self.delayed_messages[user_id].append((
                                (context.bot, {
                                    'text': '\n'.join(notification_parts),
                                    'disable_web_page_preview': True
                                }),
                                send_time
                            ))
                        else:
                            try:
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text='\n'.join(notification_parts),
                                    disable_web_page_preview=True
                                )
                            except Exception as e:
                                logger.error(f"发送通知失败: {str(e)}")
                            
                        # 处理消息转发
                        if 'forward_to' in sub.filters:
                            for target in sub.filters['forward_to']:
                                try:
                                    await message.forward(target)
                                except Exception as e:
                                    logger.error(f"转发消息失败: {str(e)}")
                                    
            # 更新统计信息
            self.update_message_stats(user_id, message_type, matched)
            
        # 检查是否是快捷键命令
        if text.startswith('#'):
            user_id = update.effective_user.id
            if user_id in self.custom_shortcuts:
                shortcut = text.split()[0]
                if shortcut in self.custom_shortcuts[user_id]:
                    command = self.custom_shortcuts[user_id][shortcut]
                    # 替换消息文本并重新处理
                    update.message.text = command + ' ' + ' '.join(text.split()[1:])
                    await self.handle_command(update, context)
                    return
                    
        # 处理过滤器组合
        for user_id, combinations in self.filter_combinations.items():
            for comb in combinations.values():
                if comb.match(text, message):
                    # 处理匹配的消息
                    # ... (使用现有的通知逻辑)
                    
        # 检查转发限制
        if 'forward_to' in sub.filters:
            if not self.check_forward_limit(user_id):
                logger.warning(f"用户 {user_id} 已达到转发限制")
                continue
                
            for target in sub.filters['forward_to']:
                try:
                    await message.forward(target)
                except Exception as e:
                    logger.error(f"转发消息失败: {str(e)}")
                    
    @error_handler
    @rate_limit(3)  # 每60秒最多3次
    async def unsubscribe(self, update, context):
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请指定要取消订阅的关键词，例如：/取消订阅 测试")
            return
            
        keyword = ' '.join(context.args)
        
        if user_id not in self.subscriptions:
            await update.message.reply_text("您当前没有任何订阅")
            return
            
        # 查找要删除的订阅
        for sub in self.subscriptions[user_id]:
            if sub.keyword == keyword:
                self.subscriptions[user_id].remove(sub)
                await update.message.reply_text(f"已取消订阅关键词：{keyword}")
                if not self.subscriptions[user_id]:  # 如果用户没有其他订阅了
                    del self.subscriptions[user_id]
                return
                
        await update.message.reply_text("未找到该关键词的订阅")
        
    @error_handler
    @rate_limit(10)  # 每60秒最多10次
    async def list_subscriptions(self, update, context):
        user_id = update.effective_user.id
        if user_id not in self.subscriptions or not self.subscriptions[user_id]:
            await update.message.reply_text("您当前没有任何订阅")
            return
            
        subscription_list = []
        for i, sub in enumerate(self.subscriptions[user_id], 1):
            sub_info = [f"{i}. 关键词：{sub.keyword} ({'正则表达式' if sub.is_regex else '普通文本'})"]
            
            # 添加过滤条件信息
            if sub.filters:
                sub_info.append("过滤条件：")
                if 'message_types' in sub.filters:
                    sub_info.append(f"- 消息类型：{','.join(sub.filters['message_types'])}")
                if 'sender_types' in sub.filters:
                    sub_info.append(f"- 发送者类型：{','.join(sub.filters['sender_types'])}")
                if 'time_range' in sub.filters:
                    start, end = sub.filters['time_range']
                    sub_info.append(f"- 时间段：{start}-{end}")
                if 'chat_ids' in sub.filters:
                    chat_ids = ','.join(map(str, sub.filters['chat_ids']))
                    sub_info.append(f"- 指定群组/频道：{chat_ids}")
                if 'min_length' in sub.filters:
                    sub_info.append(f"- 最小长度：{sub.filters['min_length']}")
                if 'max_length' in sub.filters:
                    sub_info.append(f"- 最大长度：{sub.filters['max_length']}")
                if 'exclude_keywords' in sub.filters:
                    sub_info.append(f"- 排除关键词：{','.join(sub.filters['exclude_keywords'])}")
                if 'allow_forwarded' in sub.filters:
                    sub_info.append(f"- 包含转发：{'是' if sub.filters['allow_forwarded'] else '否'}")
                if 'check_media_caption' in sub.filters:
                    sub_info.append(f"- 检查媒体标题：{'是' if sub.filters['check_media_caption'] else '否'}")
                if 'link' in sub.filters:
                    sub_info.append(f"- 包含消息链接：{'是' if sub.filters['link'] else '否'}")
                if 'quote' in sub.filters:
                    sub_info.append(f"- 包含引用消息：{'是' if sub.filters['quote'] else '否'}")
                if 'context' in sub.filters:
                    sub_info.append(f"- 显示匹配消息前后的行数：{sub.filters['context']}")
                    
            subscription_list.append('\n'.join(sub_info))
            
        await update.message.reply_text(
            "您当前的订阅列表：\n\n" + "\n\n".join(subscription_list)
        )
    
    async def add_blacklist(self, update, context):
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请定要加入黑名单的用户ID")
            return
            
        try:
            blocked_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("无效的用户ID")
            return
            
        if user_id not in self.blacklist:
            self.blacklist[user_id] = []
        if blocked_id not in self.blacklist[user_id]:
            self.blacklist[user_id].append(blocked_id)
            await update.message.reply_text(f"已将用户 {blocked_id} 加入黑名单")
        else:
            await update.message.reply_text("该用户已在黑名单中")
            
    async def remove_blacklist(self, update, context):
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请指定要移除的用户ID")
            return
            
        try:
            blocked_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("无效的用户ID")
            return
            
        if user_id in self.blacklist and blocked_id in self.blacklist[user_id]:
            self.blacklist[user_id].remove(blocked_id)
            await update.message.reply_text(f"已将用户 {blocked_id} 从黑名单中移除")
        else:
            await update.message.reply_text("该用户不在黑名单中")
            
    async def list_blacklist(self, update, context):
        user_id = update.effective_user.id
        if user_id not in self.blacklist or not self.blacklist[user_id]:
            await update.message.reply_text("您的黑名单为空")
            return
            
        blacklist_text = "\n".join(f"- {blocked_id}" for blocked_id in self.blacklist[user_id])
        await update.message.reply_text(f"您的黑名单：\n{blacklist_text}")
    
    async def stats(self, update, context):
        """显示用户的统计信息"""
        user_id = update.effective_user.id
        if user_id not in self.stats:
            await update.message.reply_text("暂无统计信息")
            return
            
        user_stats = self.stats[user_id]
        stats_text = (
            f"统计信息：\n"
            f"- 总匹配次数：{user_stats['matches']}\n"
            f"- 最后匹配时间：{user_stats['last_match']}\n"
            f"- 当前订阅数量：{len(self.subscriptions.get(user_id, []))}"
        )
        await update.message.reply_text(stats_text)
        
    async def create_group(self, update, context):
        """创建订阅分组"""
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请指定分组名称")
            return
            
        group_name = context.args[0]
        if user_id not in self.subscription_groups:
            self.subscription_groups[user_id] = {}
            
        if group_name in self.subscription_groups[user_id]:
            await update.message.reply_text("该分组名称已存在")
            return
            
        self.subscription_groups[user_id][group_name] = []
        await update.message.reply_text(f"已建分组：{group_name}")
        
    async def add_to_group(self, update, context):
        """将订阅添加到分组"""
        user_id = update.effective_user.id
        if len(context.args) < 2:
            await update.message.reply_text("请指定分组名称和关键词")
            return
            
        group_name = context.args[0]
        keyword = ' '.join(context.args[1:])
        
        if (user_id not in self.subscription_groups or 
            group_name not in self.subscription_groups[user_id]):
            await update.message.reply_text("分组不存在")
            return
            
        # 查找订阅
        subscription = None
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                subscription = sub
                break
                
        if not subscription:
            await update.message.reply_text("未找到该关键词的订阅")
            return
            
        self.subscription_groups[user_id][group_name].append(subscription)
        await update.message.reply_text(f"已将关键词 {keyword} 添加到分组 {group_name}")
        
    async def list_groups(self, update, context):
        """列出所有分组及其订阅"""
        user_id = update.effective_user.id
        if user_id not in self.subscription_groups:
            await update.message.reply_text("您没有任何分组")
            return
            
        groups_text = []
        for group_name, subs in self.subscription_groups[user_id].items():
            group_info = [f"分组：{group_name}"]
            for i, sub in enumerate(subs, 1):
                group_info.append(f"{i}. {sub.keyword}")
            groups_text.append('\n'.join(group_info))
            
        await update.message.reply_text(
            "订阅分组列表：\n\n" + "\n\n".join(groups_text)
        )
        
    async def export_subscriptions(self, update, context):
        """导出用户的订阅配置"""
        user_id = update.effective_user.id
        if user_id not in self.subscriptions:
            await update.message.reply_text("您没有任何订阅可导出")
            return
            
        export_data = {
            'subscriptions': [
                {
                    'keyword': sub.keyword,
                    'is_regex': sub.is_regex,
                    'filters': sub.filters
                }
                for sub in self.subscriptions[user_id]
            ],
            'groups': self.subscription_groups.get(user_id, {}),
            'blacklist': self.blacklist.get(user_id, []),
            'monitored_channels': self.monitored_channels.get(user_id, [])
        }
        
        # 将导出数据转换为YAML格式
        yaml_data = yaml.dump(export_data, allow_unicode=True)
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write(yaml_data)
            temp_path = f.name
            
        # 发送文件
        try:
            with open(temp_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    filename='subscriptions_export.yml',
                    caption="您的订阅配置导出文件"
                )
        finally:
            os.unlink(temp_path)
            
    async def import_subscriptions(self, update, context):
        """导入订阅配置"""
        user_id = update.effective_user.id
        if not update.message.document:
            await update.message.reply_text("请发送YAML格式的配置文件")
            return
            
        try:
            file = await context.bot.get_file(update.message.document.file_id)
            yaml_data = await file.download_as_bytearray()
            import_data = yaml.safe_load(yaml_data.decode('utf-8'))
            
            # 导入订阅
            self.subscriptions[user_id] = [
                Subscription(
                    sub['keyword'],
                    sub['is_regex'],
                    sub['filters']
                )
                for sub in import_data['subscriptions']
            ]
            
            # 导入分组
            if 'groups' in import_data:
                self.subscription_groups[user_id] = import_data['groups']
                
            # 导入黑名单
            if 'blacklist' in import_data:
                self.blacklist[user_id] = import_data['blacklist']
                
            # 导入监控频道
            if 'monitored_channels' in import_data:
                self.monitored_channels[user_id] = import_data['monitored_channels']
                
            await update.message.reply_text("配置导入成功")
        except Exception as e:
            await update.message.reply_text(f"导入失败：{str(e)}")
            
    async def set_template(self, update, context):
        """设置订阅的自定义通知模板"""
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text(
                "请指定关键词和模板，例如：\n"
                "/设置模板 测试 {keyword} 在 {group_name} 被提及"
            )
            return
            
        keyword = context.args[0]
        template = ' '.join(context.args[1:])
        
        # 查找订阅
        subscription = None
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                subscription = sub
                break
                
        if not subscription:
            await update.message.reply_text("未找到该关键词的订阅")
            return
            
        try:
            # 验证模板格式
            test_data = {
                'keyword': '测试',
                'group_name': '测试群组',
                'sender_id': '123456789',
                'sender_username': '@test',
                'sender_name': '测试用户',
                'source': '测试来源'
            }
            template.format(**test_data)
            subscription.template = template
            await update.message.reply_text("模板设置成功")
        except KeyError as e:
            await update.message.reply_text(f"模板格式错误：缺少必要的字段 {str(e)}")
            
    async def toggle_subscription(self, update, context):
        """启用/禁用订阅"""
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请指定关键词和状态，例如：/开关订阅 测试 off")
            return
            
        if len(context.args) < 2:
            await update.message.reply_text("请同时指定关键词和状态(on/off)")
            return
            
        keyword = context.args[0]
        state = context.args[1].lower()
        
        if state not in ['on', 'off']:
            await update.message.reply_text("状态必须是 on 或 off")
            return
            
        # 查找订阅
        subscription = None
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                subscription = sub
                break
                
        if not subscription:
            await update.message.reply_text("未找到该关键词的订阅")
            return
            
        subscription.enabled = (state == 'on')
        await update.message.reply_text(
            f"已{'启用' if state == 'on' else '禁用'}关键词：{keyword}"
        )
        
    async def batch_subscribe(self, update, context):
        """批量添加订阅"""
        user_id = update.effective_user.id
        if not update.message.text:
            await update.message.reply_text(
                "请按以下格式发送批量订阅命令：\n"
                "/批量订阅\n"
                "关键词1 --type=text\n"
                "关键词2 --sender=admin\n"
                "关键词3 --time=9:00-18:00"
            )
            return
            
        lines = update.message.text.split('\n')[1:]  # 跳过命令行
        if not lines:
            await update.message.reply_text("请提供要订阅的关键词列表")
            return
            
        results = []
        for line in lines:
            if not line.strip():
                continue
                
            parts = line.split()
            keyword = parts[0]
            filters = {}
            
            # 解析过选项
            for part in parts[1:]:
                if part.startswith('--'):
                    try:
                        key, value = part[2:].split('=')
                        if key == 'type':
                            filters['message_types'] = value.split(',')
                        elif key == 'sender':
                            filters['sender_types'] = value.split(',')
                        elif key == 'time':
                            start, end = value.split('-')
                            filters['time_range'] = (start, end)
                        elif key == 'chat':
                            filters['chat_ids'] = [int(x) for x in value.split(',')]
                        elif key == 'min':
                            filters['min_length'] = int(value)
                        elif key == 'max':
                            filters['max_length'] = int(value)
                        elif key == 'exclude':
                            filters['exclude_keywords'] = value.split(',')
                        elif key == 'forward':
                            filters['allow_forwarded'] = value.lower() == 'yes'
                        elif key == 'media':
                            filters['check_media_caption'] = value.lower() == 'yes'
                        elif key == 'link':
                            filters['link'] = value.lower() == 'yes'
                        elif key == 'quote':
                            filters['quote'] = value.lower() == 'yes'
                        elif key == 'context':
                            filters['context'] = int(value)
                    except ValueError:
                        results.append(f"❌ {keyword}: 无效的过滤选项 {part}")
                        continue
            
            try:
                # 检查订阅限制
                if len(self.subscriptions.get(user_id, [])) >= self.config.monitor['max_keywords_per_user']:
                    results.append(f"❌ {keyword}: 已达到最大订阅数量限制")
                    continue
                    
                # 添加订阅
                if user_id not in self.subscriptions:
                    self.subscriptions[user_id] = []
                self.subscriptions[user_id].append(Subscription(keyword, False, filters))
                results.append(f"✅ {keyword}: 订阅成功")
            except Exception as e:
                results.append(f"❌ {keyword}: {str(e)}")
                
        await update.message.reply_text("\n".join(results))
        
    async def set_delay(self, update, context):
        """设置消息延迟发送时间"""
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text(
                "请指定延迟时间（秒），例如：/设置延迟 60"
            )
            return
            
        try:
            delay = int(context.args[0])
            if delay < 0 or delay > 3600:  # 限制延迟时间在1小时内
                raise ValueError
        except ValueError:
            await update.message.reply_text("延迟时间必须是0-3600之间的整数")
            return
            
        # 更新用户的所有订阅
        for sub in self.subscriptions.get(user_id, []):
            sub.filters['delay'] = delay
            
        await update.message.reply_text(f"已设置消息延迟发送时间为 {delay} 秒")
        
    async def set_format(self, update, context):
        """设置消息格式化选项"""
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text(
                "请指定格式化选项，例如：\n"
                "/设置格式 --bold=yes --italic=no --code=no"
            )
            return
            
        format_options = {}
        for arg in context.args:
            if arg.startswith('--'):
                try:
                    key, value = arg[2:].split('=')
                    format_options[key] = (value.lower() == 'yes')
                except ValueError:
                    await update.message.reply_text(f"无效的格式化选项：{arg}")
                    return
                    
        # 更新用户的所有订阅
        for sub in self.subscriptions.get(user_id, []):
            sub.filters['format'] = format_options
            
        await update.message.reply_text("消息格式化选项设置成功")
        
    def format_message(self, text: str, format_options: dict) -> str:
        """根据格式化选项处理消息文本"""
        if not format_options:
            return text
            
        if format_options.get('bold'):
            text = f"*{text}*"
        if format_options.get('italic'):
            text = f"_{text}_"
        if format_options.get('code'):
            text = f"`{text}`"
            
        return text
        
    def is_duplicate_message(self, user_id: int, message_text: str) -> bool:
        """检查是否是重复消息"""
        current_time = datetime.now().timestamp()
        message_hash = hash(message_text)
        
        # 初始化用户的消息历史
        if user_id not in self.message_history:
            self.message_history[user_id] = []
            
        # 清理过期的消息记录（默认保留10分钟）
        self.message_history[user_id] = [
            (h, t) for h, t in self.message_history[user_id]
            if current_time - t < 600
        ]
        
        # 检查是否存在重复消息
        for h, _ in self.message_history[user_id]:
            if h == message_hash:
                return True
                
        # 添加新消息记录
        self.message_history[user_id].append((message_hash, current_time))
        return False
        
    async def process_delayed_messages(self):
        """处理延迟发送的消息"""
        current_time = datetime.now().timestamp()
        
        for user_id, messages in list(self.delayed_messages.items()):
            # 获取需要发送的消息
            messages_to_send = [
                (msg, time) for msg, time in messages
                if current_time >= time
            ]
            
            # 更新待发送队列
            self.delayed_messages[user_id] = [
                (msg, time) for msg, time in messages
                if current_time < time
            ]
            
            # 发送消息
            for message, _ in messages_to_send:
                try:
                    await message[0].copy(
                        chat_id=user_id,
                        **message[1]
                    )
                except Exception as e:
                    logger.error(f"发送延迟消息失败: {str(e)}")
                    
    async def set_priority(self, update, context):
        """设置订阅的优先级"""
        user_id = update.effective_user.id
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定关键词和优先级，例如：\n"
                "/设置优先级 测试 1\n"
                "优先级范围：0-9，数字越大优先级越高"
            )
            return
            
        keyword = context.args[0]
        try:
            priority = int(context.args[1])
            if priority < 0 or priority > 9:
                raise ValueError
        except ValueError:
            await update.message.reply_text("优先级必须是0-9之间的整数")
            return
            
        # 更新订阅优先级
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                sub.filters['priority'] = priority
                await update.message.reply_text(f"已设置关键词 {keyword} 的优先级为 {priority}")
                return
                
        await update.message.reply_text("未找到该关键词的订阅")
        
    async def set_aggregate(self, update, context):
        """设置消息聚合"""
        user_id = update.effective_user.id
        if len(context.args) < 3:
            await update.message.reply_text(
                "请指定关键词、是否聚合和时间间隔，例如：\n"
                "/设置聚合 测试 yes 300\n"
                "时间间隔单位为秒"
            )
            return
            
        keyword = context.args[0]
        aggregate = context.args[1].lower() == 'yes'
        try:
            interval = int(context.args[2])
            if interval < 60 or interval > 3600:
                raise ValueError
        except ValueError:
            await update.message.reply_text("时间间隔必须是60-3600之间的整数")
            return
            
        # 更新订阅聚合设置
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                sub.filters['aggregate'] = aggregate
                sub.filters['aggregate_interval'] = interval
                await update.message.reply_text(
                    f"已{'开启' if aggregate else '关闭'}关键词 {keyword} 的消息聚合\n"
                    f"聚合时间间隔：{interval}秒"
                )
                return
                
        await update.message.reply_text("未找到该关键词的订阅")
        
    async def send_aggregated_messages(self, user_id: int, keyword: str):
        """发送聚合消息"""
        if (user_id not in self.aggregated_messages or 
            keyword not in self.aggregated_messages[user_id]):
            return
            
        messages = self.aggregated_messages[user_id][keyword]
        if not messages:
            return
            
        # 构建聚合消息
        aggregated_text = [
            f"关键词 {keyword} 的聚合息 (共{len(messages)}条)："
        ]
        
        for msg in messages:
            aggregated_text.append(
                f"\n- 来自：{msg['group_name']}\n"
                f"  内容：{msg['text']}\n"
                f"  时间：{msg['time']}"
            )
            
        try:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="\n".join(aggregated_text),
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"发送聚合消息失败: {str(e)}")
            
        # 清空已发送的消息
        self.aggregated_messages[user_id][keyword] = []
        
    async def cleanup_old_data(self):
        """定期清理过期数据"""
        current_time = datetime.now()
        
        # 每小时执行一次清理
        if (current_time - self.last_cleanup).total_seconds() < 3600:
            return
            
        logger.info("开始清理过期数据...")
        
        # 清理过期的消息历史
        for user_id in list(self.message_history.keys()):
            self.message_history[user_id] = [
                (h, t) for h, t in self.message_history[user_id]
                if current_time.timestamp() - t < 600
            ]
            
        # 清理空的聚合消息队列
        for user_id in list(self.aggregated_messages.keys()):
            for keyword in list(self.aggregated_messages[user_id].keys()):
                if not self.aggregated_messages[user_id][keyword]:
                    del self.aggregated_messages[user_id][keyword]
            if not self.aggregated_messages[user_id]:
                del self.aggregated_messages[user_id]
                
        self.last_cleanup = current_time
        logger.info("数据清理完成")
        
    async def add_tag(self, update, context):
        """为订阅添加标签"""
        user_id = update.effective_user.id
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定关键词和标签，例如：\n"
                "/添加标签 测试 重要,工作"
            )
            return
            
        keyword = context.args[0]
        tags = context.args[1].split(',')
        
        # 查找订阅
        subscription = None
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                subscription = sub
                break
                
        if not subscription:
            await update.message.reply_text("未找到该关键词的订阅")
            return
            
        # 更新标签
        if 'tags' not in subscription.filters:
            subscription.filters['tags'] = []
        subscription.filters['tags'].extend(tags)
        subscription.filters['tags'] = list(set(subscription.filters['tags']))  # 去重
        
        # 更新用户的标签集合
        if user_id not in self.tags:
            self.tags[user_id] = set()
        self.tags[user_id].update(tags)
        
        await update.message.reply_text(
            f"已为关键词 {keyword} 添加标签：{', '.join(tags)}"
        )
        
    async def remove_tag(self, update, context):
        """移除订阅的标签"""
        user_id = update.effective_user.id
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定关键词和要移除的标签，例如：\n"
                "/移除标签 测试 重要"
            )
            return
            
        keyword = context.args[0]
        tag = context.args[1]
        
        # 查找订阅
        subscription = None
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                subscription = sub
                break
                
        if not subscription or 'tags' not in subscription.filters:
            await update.message.reply_text("未找到该关键词的标签")
            return
            
        if tag in subscription.filters['tags']:
            subscription.filters['tags'].remove(tag)
            await update.message.reply_text(f"已移除标签：{tag}")
        else:
            await update.message.reply_text("未找到该标签")
            
    async def set_note(self, update, context):
        """设置订阅备注"""
        user_id = update.effective_user.id
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定关键词和备注内容，例如：\n"
                "/设置备注 测试 这是一个测试关键词"
            )
            return
            
        keyword = context.args[0]
        note = ' '.join(context.args[1:])
        
        # 查找订阅
        subscription = None
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                subscription = sub
                break
                
        if not subscription:
            await update.message.reply_text("未找到该关键词的订阅")
            return
            
        subscription.filters['note'] = note
        await update.message.reply_text(f"已设置备注：{note}")
        
    async def set_forward(self, update, context):
        """设置消息转发目标"""
        user_id = update.effective_user.id
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定关键词和转发目标ID，例如：\n"
                "/设置转发 测试 -1001234567890,@channel_name"
            )
            return
            
        keyword = context.args[0]
        targets = context.args[1].split(',')
        
        # 查找订阅
        subscription = None
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                subscription = sub
                break
                
        if not subscription:
            await update.message.reply_text("未找到该关键词的订阅")
            return
            
        # 验证转发目标
        valid_targets = []
        for target in targets:
            try:
                if target.startswith('@'):
                    chat = await context.bot.get_chat(target)
                else:
                    chat = await context.bot.get_chat(int(target))
                valid_targets.append(chat.id)
            except Exception as e:
                await update.message.reply_text(f"无法访问目标：{target}\n错误：{str(e)}")
                
        if valid_targets:
            subscription.filters['forward_to'] = valid_targets
            await update.message.reply_text("转发目标设置成功")
            
    async def list_tags(self, update, context):
        """列出所有标签"""
        user_id = update.effective_user.id
        if user_id not in self.tags or not self.tags[user_id]:
            await update.message.reply_text("您没有任何标签")
            return
            
        tag_list = sorted(self.tags[user_id])
        tag_text = "您的标签列表：\n" + "\n".join(f"- {tag}" for tag in tag_list)
        await update.message.reply_text(tag_text)
        
    async def search_by_tag(self, update, context):
        """按标签搜索订阅"""
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请指定要搜索的标签")
            return
            
        tag = context.args[0]
        
        if user_id not in self.subscriptions:
            await update.message.reply_text("您没有任何订阅")
            return
            
        matching_subs = []
        for sub in self.subscriptions[user_id]:
            if 'tags' in sub.filters and tag in sub.filters['tags']:
                matching_subs.append(sub)
                
        if not matching_subs:
            await update.message.reply_text(f"未找到标签为 {tag} 的订阅")
            return
            
        result_text = [f"标签 {tag} 的订阅列表："]
        for sub in matching_subs:
            sub_info = [f"- 关键词：{sub.keyword}"]
            if 'note' in sub.filters:
                sub_info.append(f"  备注：{sub.filters['note']}")
            result_text.append("\n".join(sub_info))
            
        await update.message.reply_text("\n".join(result_text))
        
    async def verify_telegram(self):
        """处理 Telegram API 验证"""
        try:
            logger.info("正在进行 Telegram API 验证...")
            
            # 等待用户输入验证码
            verification_code = input("请输入 Telegram 发送的验证码: ")
            
            # 如果需要输入密码
            if verification_code.lower() == "password":
                password = input("请输入您的 Telegram 账户密码: ")
                # 处理密码验证
                # ...
            
            logger.info("Telegram API 验证成功")
            self.verified = True
            
        except Exception as e:
            logger.error(f"Telegram API 验证失败: {str(e)}")
            raise
    
    async def export_filter_rules(self, update, context):
        """导出过滤规则"""
        user_id = update.effective_user.id
        if user_id not in self.filter_rules:
            await update.message.reply_text("您没有任何过滤规则")
            return
            
        rules_data = {
            'filter_rules': self.filter_rules[user_id]
        }
        
        # 将规转换为YAML格式
        yaml_data = yaml.dump(rules_data, allow_unicode=True)
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write(yaml_data)
            temp_path = f.name
            
        try:
            with open(temp_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    filename='filter_rules.yml',
                    caption="您的过滤规则导出文件"
                )
        finally:
            os.unlink(temp_path)
            
    async def import_filter_rules(self, update, context):
        """导入过滤规则"""
        user_id = update.effective_user.id
        if not update.message.document:
            await update.message.reply_text("请发送YAML格式的规则文件")
            return
            
        try:
            file = await context.bot.get_file(update.message.document.file_id)
            yaml_data = await file.download_as_bytearray()
            rules_data = yaml.safe_load(yaml_data.decode('utf-8'))
            
            self.filter_rules[user_id] = rules_data['filter_rules']
            await update.message.reply_text("过滤规则导入成功")
        except Exception as e:
            await update.message.reply_text(f"导入失败：{str(e)}")
            
    async def add_scheduled_task(self, update, context):
        """添加定时任务"""
        user_id = update.effective_user.id
        if len(context.args) < 3:
            await update.message.reply_text(
                "请指定任务类型、时间和参数，例如：\n"
                "/添加任务 export 18:00 daily\n"
                "支持的任务类型：export（导出配置）, stats（统计信息）"
            )
            return
            
        task_type = context.args[0]
        time = context.args[1]
        frequency = context.args[2]
        
        if task_type not in ['export', 'stats']:
            await update.message.reply_text("不支持的任务类型")
            return
            
        try:
            # 验证时间格式
            hour, minute = map(int, time.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            await update.message.reply_text("无效的时间格式，请使用 HH:MM 格式")
            return
            
        if frequency not in ['daily', 'weekly']:
            await update.message.reply_text("频率必须是 daily 或 weekly")
            return
            
        if user_id not in self.scheduled_tasks:
            self.scheduled_tasks[user_id] = []
            
        self.scheduled_tasks[user_id].append({
            'type': task_type,
            'time': time,
            'frequency': frequency
        })
        
        await update.message.reply_text(
            f"已添加定时任务：\n"
            f"类型：{task_type}\n"
            f"时间：{time}\n"
            f"频率：{frequency}"
        )
        
    async def list_scheduled_tasks(self, update, context):
        """列出定时任务"""
        user_id = update.effective_user.id
        if user_id not in self.scheduled_tasks or not self.scheduled_tasks[user_id]:
            await update.message.reply_text("您没有任何定时任务")
            return
            
        tasks_text = []
        for i, task in enumerate(self.scheduled_tasks[user_id], 1):
            tasks_text.append(
                f"{i}. 类型：{task['type']}\n"
                f"   时间：{task['time']}\n"
                f"   频率：{task['frequency']}"
            )
            
        await update.message.reply_text(
            "您的定时任务列表：\n\n" + "\n\n".join(tasks_text)
        )
        
    async def remove_scheduled_task(self, update, context):
        """删除定时任务"""
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("请指定要删除的任务序号")
            return
            
        try:
            task_index = int(context.args[0]) - 1
            if user_id in self.scheduled_tasks and 0 <= task_index < len(self.scheduled_tasks[user_id]):
                removed_task = self.scheduled_tasks[user_id].pop(task_index)
                await update.message.reply_text(
                    f"已删除任务：\n"
                    f"类型：{removed_task['type']}\n"
                    f"时间：{removed_task['time']}"
                )
            else:
                await update.message.reply_text("无效的任务序号")
        except ValueError:
            await update.message.reply_text("请输入有效的数字")
            
    async def show_message_stats(self, update, context):
        """显示消息统计信息"""
        user_id = update.effective_user.id
        if user_id not in self.message_stats:
            await update.message.reply_text("暂无统计信息")
            return
            
        stats = self.message_stats[user_id]
        stats_text = [
            "消息统计信息：",
            f"总消息数：{stats['total']}",
            f"匹配消息数：{stats['matched']}",
            f"匹配率：{(stats['matched'] / stats['total'] * 100):.2f}%",
            "\n按消息类型统计："
        ]
        
        for msg_type, count in stats['types'].items():
            stats_text.append(f"- {msg_type}: {count}")
            
        await update.message.reply_text("\n".join(stats_text))
        
    def update_message_stats(self, user_id: int, message_type: str, matched: bool):
        """更新消息统计信息"""
        if user_id not in self.message_stats:
            self.message_stats[user_id] = {
                'total': 0,
                'matched': 0,
                'types': {}
            }
            
        stats = self.message_stats[user_id]
        stats['total'] += 1
        if matched:
            stats['matched'] += 1
            
        if message_type not in stats['types']:
            stats['types'][message_type] = 0
        stats['types'][message_type] += 1
        
    @error_handler
    @rate_limit(10)  # 每60秒最多10次
    async def broadcast(self, update, context):
        """管理员广播消息"""
        if not context.args:
            await update.message.reply_text("请输入要广播的消息内容")
            return
            
        message = ' '.join(context.args)
        success_count = 0
        fail_count = 0
        
        for user_id in self.subscriptions.keys():
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📢 管理员广播：\n\n{message}"
                )
                success_count += 1
            except Exception as e:
                logger.error(f"向用户 {user_id} 发送广播消息失败: {str(e)}")
                fail_count += 1
                
        await update.message.reply_text(
            f"广播消息发送完成：\n"
            f"- 成功：{success_count}\n"
            f"- 失败：{fail_count}"
        )
        
    @error_handler
    @admin_required
    async def set_user_permission(self, update, context):
        """设置用户权限"""
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定用户ID和权限，例如：\n"
                "/设置权限 123456789 premium"
            )
            return
            
        try:
            user_id = int(context.args[0])
            permission = context.args[1]
        except ValueError:
            await update.message.reply_text("无效的用户ID")
            return
            
        if user_id not in self.user_permissions:
            self.user_permissions[user_id] = set()
            
        self.user_permissions[user_id].add(permission)
        await update.message.reply_text(f"已为用户 {user_id} 添加权限：{permission}")
        
    async def reconnect(self):
        """处理连接断开的重连逻辑"""
        while self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                logger.info(f"尝试重新连接... (第 {self.reconnect_attempts + 1} 次)")
                # 重新创建 Application 实例
                kwargs = {}
                if self.config.proxy:
                    kwargs['proxy_url'] = (
                        f"{self.config.proxy['scheme']}://"
                        f"{self.config.proxy['hostname']}:{self.config.proxy['port']}"
                    if self.config.proxy['username'] and self.config.proxy['password']:
                        kwargs['proxy_auth'] = (
                            self.config.proxy['username'],
                            self.config.proxy['password']
                        )
                
                self.app = Application.builder().token(self.config.bot_token).build(**kwargs)
                await self.app.initialize()
                await self.app.start()
                self.reconnect_attempts = 0  # 重置重连次数
                logger.info("重连连接成功")
                return True
            except Exception as e:
                self.reconnect_attempts += 1
                logger.error(f"重连失败: {str(e)}")
                await asyncio.sleep(self.reconnect_delay)
        
        logger.error("达到最大重连次数，停止重连")
        return False
        
    def cleanup_cache(self):
        """清理过期的缓存数据"""
        # 清理消息历史
        for user_id in list(self.message_history.keys()):
            if len(self.message_history[user_id]) > self.cache_size:
                self.message_history[user_id] = self.message_history[user_id][-self.cache_size:]
                
        # 清理性能统计数据
        if len(self.performance_stats['message_processing_time']) > self.cache_size:
            self.performance_stats['message_processing_time'] = \
                self.performance_stats['message_processing_time'][-self.cache_size:]
        if len(self.performance_stats['memory_usage']) > self.cache_size:
            self.performance_stats['memory_usage'] = \
                self.performance_stats['memory_usage'][-self.cache_size:]
                
    async def monitor_performance(self):
        """监控系统性能"""
        while True:
            try:
                # 获取内存使用情况
                process = psutil.Process()
                memory_info = process.memory_info()
                self.performance_stats['memory_usage'].append({
                    'time': datetime.now(),
                    'rss': memory_info.rss,
                    'vms': memory_info.vms
                })
                
                # 清理缓存
                self.cleanup_cache()
                
                # 每分钟记录一次
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"性能监控错误: {str(e)}")
                await asyncio.sleep(60)
                
    async def get_performance_stats(self, update, context):
        """获取性能统计信息"""
        user_id = update.effective_user.id
        if user_id not in self.config.admin_users:
            await update.message.reply_text("此命令仅管理员可用")
            return
            
        # 计算运行时间
        uptime = datetime.now() - self.performance_stats['start_time']
        
        # 计算平均消息处理时间
        avg_processing_time = 0
        if self.performance_stats['message_processing_time']:
            avg_processing_time = sum(self.performance_stats['message_processing_time']) / \
                len(self.performance_stats['message_processing_time'])
        
        # 获取最新的内存使用情况
        latest_memory = self.performance_stats['memory_usage'][-1] if self.performance_stats['memory_usage'] else None
        
        stats_text = [
            "性能统计信息：",
            f"运行时间：{uptime}",
            f"平均消息处理时间：{avg_processing_time:.2f}秒",
            f"当前订阅总数：{sum(len(subs) for subs in self.subscriptions.values())}",
            f"当前用户总数：{len(self.subscriptions)}"
        ]
        
        if latest_memory:
            stats_text.extend([
                f"内存使用（RSS）：{latest_memory['rss'] / 1024 / 1024:.2f} MB",
                f"内存使用（VMS）：{latest_memory['vms'] / 1024 / 1024:.2f} MB"
            ])
            
        await update.message.reply_text("\n".join(stats_text))
        
    async def handle_message(self, update, context):
        start_time = time()
        try:
            await super().handle_message(update, context)
        finally:
            # 记录消息处理时间
            processing_time = time() - start_time
            self.performance_stats['message_processing_time'].append(processing_time)
            
    def run(self):
        try:
            # 首次运行时进行验证
            if not self.verified:
                asyncio.get_event_loop().run_until_complete(self.verify_telegram())
            
            # 注册命令处理器
            self.app.add_handler(CommandHandler("start", self.start))
            self.app.add_handler(CommandHandler("help", self.help))
            self.app.add_handler(CommandHandler(["订阅", "订阅正则"], self.subscribe))
            self.app.add_handler(CommandHandler("取消订阅", self.unsubscribe))
            self.app.add_handler(CommandHandler("我的订阅", self.list_subscriptions))
            self.app.add_handler(CommandHandler("添加频道", self.add_channel))
            self.app.add_handler(CommandHandler("删除频道", self.remove_channel))
            self.app.add_handler(CommandHandler("频道列表", self.list_channels))
            self.app.add_handler(CommandHandler("黑名单", self.add_blacklist))
            self.app.add_handler(CommandHandler("移除黑名单", self.remove_blacklist))
            self.app.add_handler(CommandHandler("黑名单列表", self.list_blacklist))
            self.app.add_handler(CommandHandler("统计", self.stats))
            self.app.add_handler(CommandHandler("创建分组", self.create_group))
            self.app.add_handler(CommandHandler("添加到分组", self.add_to_group))
            self.app.add_handler(CommandHandler("分组列表", self.list_groups))
            self.app.add_handler(CommandHandler("导出配置", self.export_subscriptions))
            self.app.add_handler(CommandHandler("导入配置", self.import_subscriptions))
            self.app.add_handler(CommandHandler("设置模板", self.set_template))
            self.app.add_handler(CommandHandler("开关订阅", self.toggle_subscription))
            self.app.add_handler(CommandHandler("批量订阅", self.batch_subscribe))
            self.app.add_handler(CommandHandler("设置延迟", self.set_delay))
            self.app.add_handler(CommandHandler("设置格式", self.set_format))
            self.app.add_handler(CommandHandler("设置优先级", self.set_priority))
            self.app.add_handler(CommandHandler("设置聚合", self.set_aggregate))
            self.app.add_handler(CommandHandler("添加标签", self.add_tag))
            self.app.add_handler(CommandHandler("移除标签", self.remove_tag))
            self.app.add_handler(CommandHandler("设置备注", self.set_note))
            self.app.add_handler(CommandHandler("设置转发", self.set_forward))
            self.app.add_handler(CommandHandler("标签列表", self.list_tags))
            self.app.add_handler(CommandHandler("搜索标签", self.search_by_tag))
            self.app.add_handler(CommandHandler("导出规则", self.export_filter_rules))
            self.app.add_handler(CommandHandler("导入规则", self.import_filter_rules))
            self.app.add_handler(CommandHandler("添加任务", self.add_scheduled_task))
            self.app.add_handler(CommandHandler("任务列表", self.list_scheduled_tasks))
            self.app.add_handler(CommandHandler("删除任务", self.remove_scheduled_task))
            self.app.add_handler(CommandHandler("消息统计", self.show_message_stats))
            self.app.add_handler(CommandHandler("广播", self.broadcast))
            self.app.add_handler(CommandHandler("设置权限", self.set_user_permission))
            
            # 注册消息处理器
            self.app.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.handle_message
            ))
            
            logger.info("机器人启动成功")
            
            # 添加性能监控命令
            self.app.add_handler(CommandHandler("性能统计", self.get_performance_stats))
            
            # 启动性能监控
            asyncio.create_task(self.monitor_performance())
            
            # 启动机器人
            self.app.run_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "edited_message", "channel_post"],
                close_loop=False
            )
            
        except Exception as e:
            logger.error(f"运行错误: {str(e)}")
            # 尝试重连
            if asyncio.run(self.reconnect()):
                self.run()  # 重新运行
            else:
                raise
            
            # 添加新的命令处理器
            self.app.add_handler(CommandHandler("设置加密", self.set_encryption))
            self.app.add_handler(CommandHandler("设置配额", self.set_quota))
            self.app.add_handler(CommandHandler("恢复备份", self.restore_backup))
            
            # 启动自动备份任务
            asyncio.create_task(self.auto_backup())
            
            # 添加新的命令处理器
            self.app.add_handler(CommandHandler("设置别名", self.set_command_alias))
            self.app.add_handler(CommandHandler("别名列表", self.list_aliases))
            self.app.add_handler(CommandHandler("设置命令权限", self.set_command_level))
            
            # 启动会话清理任务
            asyncio.create_task(self.session_cleanup_task())
            
            # 添加新的命令处理器
            self.app.add_handler(CommandHandler("设置转发限制", self.set_forward_limit))
            self.app.add_handler(CommandHandler("添加快捷键", self.add_shortcut))
            self.app.add_handler(CommandHandler("创建组合", self.create_filter_combination))
            self.app.add_handler(CommandHandler("组合列表", self.list_combinations))
            
            # 添加新的命令处理器
            self.app.add_handler(CommandHandler("保存模板", self.save_filter_template))
            self.app.add_handler(CommandHandler("应用模板", self.apply_filter_template))
            self.app.add_handler(CommandHandler("模板列表", self.list_filter_templates))
            self.app.add_handler(CommandHandler("操作日志", self.view_user_logs))
            
            # 启动清理任务
            asyncio.create_task(self.cleanup_task())
            
        except Exception as e:
            logger.error(f"运行错误: {str(e)}")
            # 尝试重连
            if asyncio.run(self.reconnect()):
                self.run()  # 重新运行
            else:
                raise
            
    async def auto_backup(self):
        """自动备份任务"""
        while True:
            try:
                current_time = datetime.now()
                if (current_time - self.last_backup) >= self.backup_interval:
                    await self.create_backup()
                await asyncio.sleep(3600)  # 每小时检查一次
            except Exception as e:
                logger.error(f"自动备份失败: {str(e)}")
                await asyncio.sleep(3600)
            
    async def encrypt_message(self, message: str) -> str:
        """加密消息"""
        try:
            return self.cipher_suite.encrypt(message.encode()).decode()
        except Exception as e:
            logger.error(f"消息加密失败: {str(e)}")
            return message
            
    async def decrypt_message(self, encrypted_message: str) -> str:
        """解密消息"""
        try:
            return self.cipher_suite.decrypt(encrypted_message.encode()).decode()
        except Exception as e:
            logger.error(f"消息解密失败: {str(e)}")
            return encrypted_message
            
    async def set_encryption(self, update, context):
        """设置是否加密消息"""
        user_id = update.effective_user.id
        if not context.args or context.args[0] not in ['on', 'off']:
            await update.message.reply_text("请指定是否启用加密：/设置加密 on/off")
            return
            
        enable_encryption = context.args[0] == 'on'
        
        # 更新用户的所有订阅
        for sub in self.subscriptions.get(user_id, []):
            sub.filters['encrypt'] = enable_encryption
            
        await update.message.reply_text(
            f"已{'启用' if enable_encryption else '禁用'}消息加密"
        )
        
    async def set_quota(self, update, context):
        """设置用户配额"""
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "请指定用户ID和每日配额，例如：\n"
                "/设置配额 123456789 100"
            )
            return
            
        try:
            target_user = int(context.args[0])
            daily_quota = int(context.args[1])
        except ValueError:
            await update.message.reply_text("无效的用户ID或配额数量")
            return
            
        self.user_quotas[target_user] = {
            'daily': daily_quota,
            'used': 0,
            'last_reset': datetime.now()
        }
        
        await update.message.reply_text(
            f"已设置用户 {target_user} 的每日配额为 {daily_quota}"
        )
        
    def check_quota(self, user_id: int) -> bool:
        """检查用户配额"""
        if user_id not in self.user_quotas:
            return True  # 没有配额限制
            
        quota = self.user_quotas[user_id]
        current_time = datetime.now()
        
        # 检查是否需要重置配额
        if (current_time - quota['last_reset']).days >= 1:
            quota['used'] = 0
            quota['last_reset'] = current_time
            
        # 检查是否超过配额
        if quota['used'] >= quota['daily']:
            return False
            
        quota['used'] += 1
        return True
        
    async def create_backup(self):
        """创建配置备份"""
        current_time = datetime.now()
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        # 准备备份数据
        backup_data = {
            'subscriptions': self.subscriptions,
            'monitored_channels': self.monitored_channels,
            'blacklist': self.blacklist,
            'user_quotas': self.user_quotas,
            'stats': self.stats,
            'filter_rules': self.filter_rules,
            'scheduled_tasks': self.scheduled_tasks
        }
        
        # 创建备份文件
        backup_file = os.path.join(
            backup_dir,
            f"backup_{current_time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, default=str)
            
        # 清理旧备份
        backup_files = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith("backup_")],
            reverse=True
        )
        
        for old_file in backup_files[self.max_backups:]:
            os.remove(os.path.join(backup_dir, old_file))
            
        self.last_backup = current_time
        logger.info(f"创建备份成功: {backup_file}")
        
    async def restore_backup(self, update, context):
        """从���份恢复"""
        if not context.args:
            # 列出可用的备份
            backup_dir = "backups"
            if not os.path.exists(backup_dir):
                await update.message.reply_text("没有找到任何备份")
                return
                
            backup_files = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith("backup_")],
                reverse=True
            )
            
            if not backup_files:
                await update.message.reply_text("没有找到任何备份")
                return
                
            backup_list = "\n".join(
                f"{i+1}. {f}" for i, f in enumerate(backup_files)
            await update.message.reply_text(
                f"可用的备份文件：\n{backup_list}\n"
                f"使用 /恢复备份 <编号> 来恢复指定的备份"
            )
            return
            
        try:
            backup_index = int(context.args[0]) - 1
            backup_dir = "backups"
            backup_files = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith("backup_")],
                reverse=True
            )
            
            if not 0 <= backup_index < len(backup_files):
                await update.message.reply_text("无效的备份编号")
                return
                
            backup_file = os.path.join(backup_dir, backup_files[backup_index])
            
            # 读取备份数据
            with open(backup_file, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
                
            # 恢复数据
            self.subscriptions = backup_data['subscriptions']
            self.monitored_channels = backup_data['monitored_channels']
            self.blacklist = backup_data['blacklist']
            self.user_quotas = backup_data['user_quotas']
            self.stats = backup_data['stats']
            self.filter_rules = backup_data['filter_rules']
            self.scheduled_tasks = backup_data['scheduled_tasks']
            
            await update.message.reply_text("恢复备份成功")
            
        except Exception as e:
            await update.message.reply_text(f"恢复备份失败: {str(e)}")
            
    def get_session(self, user_id: int) -> Session:
        """获取或创建用户会话"""
        if user_id not in self.sessions:
            self.sessions[user_id] = Session(user_id)
        session = self.sessions[user_id]
        session.update_activity()
        return session
        
    def cleanup_sessions(self):
        """清理过期会话"""
        current_time = datetime.now()
        expired_time = current_time - timedelta(hours=1)  # 1小时无活动则清理
        
        for user_id in list(self.sessions.keys()):
            if self.sessions[user_id].last_activity < expired_time:
                del self.sessions[user_id]
                
    async def set_command_alias(self, update, context):
        """设置命令别名"""
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定原始命令和别名，例如：\n"
                "/设置别名 订阅 sub"
            )
            return
            
        original_cmd = context.args[0]
        alias = context.args[1]
        
        if alias in self.command_aliases:
            await update.message.reply_text("该别名已被使用")
            return
            
        self.command_aliases[alias] = original_cmd
        await update.message.reply_text(f"已设置命令别名：{alias} -> {original_cmd}")
        
    async def list_aliases(self, update, context):
        """列出所有命令别名"""
        if not self.command_aliases:
            await update.message.reply_text("当前没有任何命令别名")
            return
            
        alias_list = [
            f"{alias} -> {original}"
            for alias, original in self.command_aliases.items()
        ]
        
        await update.message.reply_text(
            "命令别名列表：\n" + "\n".join(alias_list)
        )
        
    def check_command_permission(self, command: str, user_id: int) -> bool:
        """检查用户是否有权限执行命令"""
        if command not in self.command_levels:
            return True  # 未设置权限的命令默认所有人可用
            
        required_level = self.command_levels[command]
        session = self.get_session(user_id)
        return session.command_level.value >= required_level.value
        
    async def handle_command(self, update, context):
        """处理命令前的权限检查和别名解析"""
        command = context.args[0] if context.args else ""
        user_id = update.effective_user.id
        
        # 检查命令别名
        if command in self.command_aliases:
            command = self.command_aliases[command]
            
        # 检查权限
        if not self.check_command_permission(command, user_id):
            await update.message.reply_text("您没有权限执行此命令")
            return False
            
        return True
        
    async def set_command_level(self, update, context):
        """设置命令的权限等级"""
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定命令和权限等级，例如：\n"
                "/设置权限 broadcast ADMIN"
            )
            return
            
        command = context.args[0]
        try:
            level = CommandLevel[context.args[1].upper()]
        except KeyError:
            await update.message.reply_text(
                "无效的权限等级，可用选项：\n"
                "USER - 普通用户\n"
                "PREMIUM - 高级用户\n"
                "ADMIN - 管理员\n"
                "OWNER - 所有者"
            )
            return
            
        self.command_levels[command] = level
        await update.message.reply_text(
            f"已设置命令 {command} 的权限等级为 {level.name}"
        )
        
    async def session_cleanup_task(self):
        """定期清理过期会话"""
        while True:
            try:
                self.cleanup_sessions()
                await asyncio.sleep(3600)  # 每小时清理一次
            except Exception as e:
                logger.error(f"会话清理失败: {str(e)}")
                await asyncio.sleep(3600)
        
    async def set_forward_limit(self, update, context):
        """设置消息转发限制"""
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定用户ID和每日转发限制，例如：\n"
                "/设置转发限制 123456789 100"
            )
            return
            
        try:
            target_user = int(context.args[0])
            daily_limit = int(context.args[1])
        except ValueError:
            await update.message.reply_text("无效的用户ID或限制数量")
            return
            
        self.forward_limits[target_user] = {
            'daily': daily_limit,
            'used': 0,
            'last_reset': datetime.now()
        }
        
        await update.message.reply_text(
            f"已设置用户 {target_user} 的每日转发限制为 {daily_limit} 条"
        )
        
    async def add_shortcut(self, update, context):
        """添加命令快捷键"""
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定快捷键和命令，例如：\n"
                "/添加快捷键 #k /订阅"
            )
            return
            
        user_id = update.effective_user.id
        shortcut = context.args[0]
        command = context.args[1]
        
        if user_id not in self.custom_shortcuts:
            self.custom_shortcuts[user_id] = {}
            
        self.custom_shortcuts[user_id][shortcut] = command
        await update.message.reply_text(f"已添加快捷键：{shortcut} -> {command}")
        
    async def create_filter_combination(self, update, context):
        """创建过滤器组合"""
        if len(context.args) < 3:
            await update.message.reply_text(
                "请指定组合名称、操作符和过滤器列表，例如：\n"
                "/创建组合 重要消息 AND 关键词1,关键词2"
            )
            return
            
        user_id = update.effective_user.id
        name = context.args[0]
        operator = context.args[1].upper()
        filter_names = context.args[2].split(',')
        
        if operator not in ['AND', 'OR']:
            await update.message.reply_text("操作符必须是 AND 或 OR")
            return
            
        # 查找过滤器
        filters = []
        for fname in filter_names:
            found = False
            for sub in self.subscriptions.get(user_id, []):
                if sub.keyword == fname:
                    filters.append(sub)
                    found = True
                    break
            if not found:
                await update.message.reply_text(f"未找到过滤器：{fname}")
                return
                
        if user_id not in self.filter_combinations:
            self.filter_combinations[user_id] = {}
            
        self.filter_combinations[user_id][name] = FilterCombination(
            name, filters, operator
        )
        
        await update.message.reply_text(
            f"已创建过滤器组合：{name}\n"
            f"操作符：{operator}\n"
            f"包含过滤器：{', '.join(filter_names)}"
        )
        
    async def list_combinations(self, update, context):
        """列出所有过滤器组合"""
        user_id = update.effective_user.id
        if user_id not in self.filter_combinations:
            await update.message.reply_text("您没有任何过滤器组合")
            return
            
        combinations = []
        for name, comb in self.filter_combinations[user_id].items():
            filter_names = [f.keyword for f in comb.filters]
            combinations.append(
                f"组合名称：{name}\n"
                f"操作符：{comb.operator}\n"
                f"包含过滤器：{', '.join(filter_names)}"
            )
            
        await update.message.reply_text(
            "过滤器组合列表：\n\n" + "\n\n".join(combinations)
        )
        
    def check_forward_limit(self, user_id: int) -> bool:
        """检查转发限制"""
        if user_id not in self.forward_limits:
            return True
            
        limit = self.forward_limits[user_id]
        current_time = datetime.now()
        
        # 检查是否需要重置计数
        if (current_time - limit['last_reset']).days >= 1:
            limit['used'] = 0
            limit['last_reset'] = current_time
            
        if limit['used'] >= limit['daily']:
            return False
            
        limit['used'] += 1
        return True
        
    async def save_filter_template(self, update, context):
        """保存过滤器模板"""
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定模板名称和关键词，例如：\n"
                "/保存模板 重要消息 测试"
            )
            return
            
        user_id = update.effective_user.id
        template_name = context.args[0]
        keyword = context.args[1]
        
        # 查找订阅的过滤器设置
        subscription = None
        for sub in self.subscriptions.get(user_id, []):
            if sub.keyword == keyword:
                subscription = sub
                break
                
        if not subscription:
            await update.message.reply_text("未找到该关键词的订阅")
            return
            
        # 保存过滤器设置为模板
        if user_id not in self.filter_templates:
            self.filter_templates[user_id] = {}
            
        self.filter_templates[user_id][template_name] = subscription.filters.copy()
        await update.message.reply_text(f"已保存过滤器模板：{template_name}")
        
        # 记录用户操作
        self.log_user_action(user_id, "save_template", {
            "template_name": template_name,
            "keyword": keyword
        })
        
    async def apply_filter_template(self, update, context):
        """应用过滤器模板"""
        if len(context.args) < 2:
            await update.message.reply_text(
                "请指定模板名称和目标关键词，例如：\n"
                "/应用模板 重要消息 新关键词"
            )
            return
            
        user_id = update.effective_user.id
        template_name = context.args[0]
        keyword = context.args[1]
        
        if (user_id not in self.filter_templates or 
            template_name not in self.filter_templates[user_id]):
            await update.message.reply_text("未找到该模板")
            return
            
        # 应用模板到新的订阅
        template_filters = self.filter_templates[user_id][template_name].copy()
        
        if user_id not in self.subscriptions:
            self.subscriptions[user_id] = []
            
        self.subscriptions[user_id].append(
            Subscription(keyword, False, template_filters)
        )
        
        await update.message.reply_text(
            f"已使用模板 {template_name} 创建新订阅：{keyword}"
        )
        
        # 记录用户操作
        self.log_user_action(user_id, "apply_template", {
            "template_name": template_name,
            "keyword": keyword
        })
        
    async def list_filter_templates(self, update, context):
        """列出所有过滤器模板"""
        user_id = update.effective_user.id
        if user_id not in self.filter_templates:
            await update.message.reply_text("您没有保存任何过滤器模板")
            return
            
        templates_text = []
        for name, filters in self.filter_templates[user_id].items():
            template_info = [f"模板名称：{name}"]
            for key, value in filters.items():
                template_info.append(f"- {key}: {value}")
            templates_text.append("\n".join(template_info))
            
        await update.message.reply_text(
            "过滤器模板列表：\n\n" + "\n\n".join(templates_text)
        )
        
    def log_user_action(self, user_id: int, action: str, details: dict = None):
        """记录用户操作"""
        if user_id not in self.user_logs:
            self.user_logs[user_id] = []
            
        self.user_logs[user_id].append({
            'action': action,
            'time': datetime.now(),
            'details': details or {}
        })
        
    async def view_user_logs(self, update, context):
        """查看用户操作日志"""
        user_id = update.effective_user.id
        if user_id not in self.user_logs:
            await update.message.reply_text("暂无操作日志")
            return
            
        # 获取最近的日志（最多显示20条）
        recent_logs = self.user_logs[user_id][-20:]
        
        log_text = []
        for log in recent_logs:
            log_entry = [
                f"时间：{log['time'].strftime('%Y-%m-%d %H:%M:%S')}",
                f"操作：{log['action']}"
            ]
            if log['details']:
                log_entry.append("详情：")
                for key, value in log['details'].items():
                    log_entry.append(f"- {key}: {value}")
            log_text.append("\n".join(log_entry))
            
        await update.message.reply_text(
            "最近的操作日志：\n\n" + "\n\n".join(log_text)
        )
        
    async def cleanup_task(self):
        """定期清理任务"""
        while True:
            try:
                current_time = datetime.now()
                
                # 清理消息历史
                message_history_limit = current_time - timedelta(
                    days=self.cleanup_settings['message_history_days']
                )
                for user_id in list(self.message_history.keys()):
                    self.message_history[user_id] = [
                        (h, t) for h, t in self.message_history[user_id]
                        if datetime.fromtimestamp(t) > message_history_limit
                    ]
                    
                # 清理用户日志
                user_logs_limit = current_time - timedelta(
                    days=self.cleanup_settings['user_logs_days']
                )
                for user_id in list(self.user_logs.keys()):
                    self.user_logs[user_id] = [
                        log for log in self.user_logs[user_id]
                        if log['time'] > user_logs_limit
                    ]
                    
                # 清理旧备份
                backup_dir = "backups"
                if os.path.exists(backup_dir):
                    backup_limit = current_time - timedelta(
                        days=self.cleanup_settings['backup_retention_days']
                    )
                    for filename in os.listdir(backup_dir):
                        filepath = os.path.join(backup_dir, filename)
                        if (os.path.getctime(filepath) < backup_limit.timestamp() and
                            filename.startswith("backup_")):
                            os.remove(filepath)
                            
                logger.info("清理任务完成")
                await asyncio.sleep(86400)  # 每天执行一次
                
            except Exception as e:
                logger.error(f"清理任务失败: {str(e)}")
                await asyncio.sleep(3600)

    def _process_message_queue(self):
        """处理消息队列的后台线程"""
        while True:
            try:
                # 获取队列中的消息
                message = self.message_queue.get()
                if message is None:
                    break
                    
                # 保存到数据库
                queue_item = MessageQueue(
                    user_id=message['user_id'],
                    message=message['data']
                )
                self.db.add(queue_item)
                self.db.commit()
                
                # 处理消息
                try:
                    self._process_queued_message(message)
                    queue_item.processed = True
                except Exception as e:
                    queue_item.error = str(e)
                finally:
                    self.db.commit()
                    
            except Exception as e:
                logger.error(f"处理消息队列时出错: {str(e)}")
                
            finally:
                self.message_queue.task_done()
                
    def _process_queued_message(self, message):
        """处理单个队列消息"""
        user_id = message['user_id']
        data = message['data']
        
        # 根据消息类型处理
        if data['type'] == 'notification':
            self._send_notification(user_id, data['content'])
        elif data['type'] == 'subscription_update':
            self._update_subscription(user_id, data['content'])
            
    async def health_check(self):
        """执行健康检查"""
        try:
            # 检查数据库连接
            self.db.execute("SELECT 1")
            
            # 检查消息队列状态
            queue_size = self.message_queue.qsize()
            
            # 检查内存使用
            process = psutil.Process()
            memory_info = process.memory_info()
            
            # 记录健康状态
            health_status = HealthCheck(
                status="healthy",
                details={
                    'queue_size': queue_size,
                    'memory_usage': {
                        'rss': memory_info.rss,
                        'vms': memory_info.vms
                    },
                    'uptime': (datetime.now() - self.performance_stats['start_time']).total_seconds()
                }
            )
            self.db.add(health_status)
            self.db.commit()
            
            return True
            
        except Exception as e:
            logger.error(f"健康检查失败: {str(e)}")
            
            # 记录错误状态
            health_status = HealthCheck(
                status="unhealthy",
                details={'error': str(e)}
            )
            self.db.add(health_status)
            self.db.commit()
            
            return False
            
    async def get_health_status(self, update, context):
        """获取健康状态信息"""
        user_id = update.effective_user.id
        if user_id not in self.config.admin_users:
            await update.message.reply_text("此命令仅管理员可用")
            return
            
        # 获取最新的健康检查记录
        latest_check = self.db.query(HealthCheck).order_by(
            HealthCheck.check_time.desc()
        ).first()
        
        if not latest_check:
            await update.message.reply_text("暂无健康检查记录")
            return
            
        status_text = [
            f"状态：{latest_check.status}",
            f"检查时间：{latest_check.check_time}",
            "\n详细信息："
        ]
        
        for key, value in latest_check.details.items():
            if isinstance(value, dict):
                status_text.append(f"{key}:")
                for k, v in value.items():
                    status_text.append(f"  {k}: {v}")
            else:
                status_text.append(f"{key}: {value}")
                
        await update.message.reply_text("\n".join(status_text))
        
    async def _health_check_task(self):
        """定期执行健康检查"""
        while True:
            try:
                await self.health_check()
                await asyncio.sleep(300)  # 每5分钟检查一次
            except Exception as e:
                logger.error(f"健康检查任务失败: {str(e)}")
                await asyncio.sleep(60)

if __name__ == "__main__":
    bot = KeywordBot()
    bot.run() 