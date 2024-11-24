import pytest
import asyncio
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
from main import KeywordBot, Subscription, FilterCombination
from config import Config

@pytest.fixture
def bot():
    with patch('main.Config') as mock_config:
        # 模拟配置
        mock_config.return_value.bot_token = "test_token"
        mock_config.return_value.monitor = {'max_keywords_per_user': 10}
        mock_config.return_value.notification = {
            'template': "关键词：{keyword}\n来源：{group_name}"
        }
        mock_config.return_value.admin_users = [12345]
        return KeywordBot()

@pytest.mark.asyncio
async def test_subscribe(bot):
    # 模拟更新和上下文
    update = Mock()
    update.effective_user.id = 12345
    update.message = Mock()
    
    context = Mock()
    context.args = ["测试关键词", "--type=text"]
    
    # 测试订阅
    await bot.subscribe(update, context)
    
    # 验证结果
    assert 12345 in bot.subscriptions
    assert len(bot.subscriptions[12345]) == 1
    assert bot.subscriptions[12345][0].keyword == "测试关��词"

@pytest.mark.asyncio
async def test_unsubscribe(bot):
    # 预设订阅
    user_id = 12345
    bot.subscriptions[user_id] = [Subscription("测试关键词")]
    
    update = Mock()
    update.effective_user.id = user_id
    update.message = Mock()
    
    context = Mock()
    context.args = ["测试关键词"]
    
    # 测试取消订阅
    await bot.unsubscribe(update, context)
    
    # 验证结果
    assert user_id not in bot.subscriptions

@pytest.mark.asyncio
async def test_message_matching(bot):
    # 预设订阅
    user_id = 12345
    bot.subscriptions[user_id] = [Subscription("测试")]
    
    # 模拟消息
    update = Mock()
    update.message.text = "这是一条测试消息"
    update.message.chat.title = "测试群组"
    update.message.from_user.id = 67890
    update.message.from_user.username = "test_user"
    update.message.from_user.full_name = "Test User"
    update.message.chat.type = "group"
    
    context = Mock()
    
    # 测试消息匹配
    await bot.handle_message(update, context)
    
    # 验证通知发送
    context.bot.send_message.assert_called_once()

def test_filter_combination():
    # 创建测试过滤器
    filter1 = Subscription("关键词1")
    filter2 = Subscription("关键词2")
    
    # 测试 AND 组合
    comb_and = FilterCombination("测试组合", [filter1, filter2], "AND")
    assert not comb_and.match("只包含关键词1", None)
    assert comb_and.match("同时包含关键词1和关键词2", None)
    
    # 测试 OR 组合
    comb_or = FilterCombination("测试组合", [filter1, filter2], "OR")
    assert comb_or.match("只包含关键词1", None)
    assert comb_or.match("只包含关键词2", None)

@pytest.mark.asyncio
async def test_message_filters(bot):
    # 测试各种过滤条件
    user_id = 12345
    bot.subscriptions[user_id] = [
        Subscription("测试", filters={
            'message_types': ['text'],
            'sender_types': ['user'],
            'min_length': 5,
            'max_length': 100
        })
    ]
    
    update = Mock()
    update.message.text = "测试消息"
    update.message.chat.type = "group"
    update.message.from_user.id = 67890
    
    context = Mock()
    
    # 测试消息类型过滤
    await bot.handle_message(update, context)
    assert context.bot.send_message.called

@pytest.mark.asyncio
async def test_quota_limit(bot):
    user_id = 12345
    bot.user_quotas[user_id] = {
        'daily': 2,
        'used': 0,
        'last_reset': datetime.now()
    }
    
    # 测试配额限制
    assert bot.check_quota(user_id)  # 第一次：成功
    assert bot.check_quota(user_id)  # 第二次：成功
    assert not bot.check_quota(user_id)  # 第三次：失败
    
    # 测试配额重置
    bot.user_quotas[user_id]['last_reset'] = datetime.now() - timedelta(days=1)
    assert bot.check_quota(user_id)  # 重置后：成功

@pytest.mark.asyncio
async def test_encryption(bot):
    # 测试消息加密/解密
    original_message = "测试消息"
    encrypted = await bot.encrypt_message(original_message)
    decrypted = await bot.decrypt_message(encrypted)
    assert decrypted == original_message

@pytest.mark.asyncio
async def test_backup_restore(bot):
    # 预设一些数据
    user_id = 12345
    bot.subscriptions[user_id] = [Subscription("测试关键词")]
    
    # 测试备份
    await bot.create_backup()
    
    # 清除数据
    bot.subscriptions = {}
    
    # 测试恢复
    update = Mock()
    update.effective_user.id = user_id
    update.message = Mock()
    
    context = Mock()
    context.args = ["1"]  # 选择最新的备份
    
    await bot.restore_backup(update, context)
    
    # 验证数据恢复
    assert user_id in bot.subscriptions
    assert bot.subscriptions[user_id][0].keyword == "测试关键词" 