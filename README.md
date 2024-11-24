# Telegram 关键词提醒机器人

[![Version](https://img.shields.io/badge/version-1.0.1-blue.svg)](https://github.com/Wu1qq/telegram-keyword-bot.git)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-yellow.svg)](https://www.python.org)

一个功能强大的 Telegram 关键词提醒机器人，支持多种匹配模式和高级过滤功能。

## 功能特点

### 基础功能
- 支持普通文本和正则表达式匹配
- 多频道/群组监控
- 实时消息推送
- 支持私有频道订阅
- 黑名单管理

### 高级功能
- 消息过滤器（类型、发送者、时间等）
- 消息聚合
- 定时任务
- 数据备份/恢复
- 消息加密
- 用户配额管理
- 命令别名
- 消息转发限制

### 监控功能
- 性能监控
- 健康检查
- 错误告警
- 操作日志

## 快速开始

### 1. 环境要求
- 操作系统：Linux (推荐 Ubuntu 20.04+)
- Python 3.9+
- SQLite 3
- Git
- 内存 ≥1GB
- 磁盘空间 ≥10GB
- 稳定的网络连接

### 2. 安装步骤
bash
Ubuntu/Debian
sudo apt update
sudo apt install -y python3.9 python3.9-venv python3-pip git sqlite3
CentOS/RHEL
sudo yum install -y python39 python39-devel git sqlite

克隆仓库

bash
git clone https://github.com/Wu1qq/telegram-keyword-bot.git
cd telegram-keyword-bot


3. 创建虚拟环境
bash
python3.9 -m venv venv
source venv/bin/activate # Linux/Mac
或
venv\Scripts\activate # Windows


4. 安装依赖
bash
pip install --upgrade pip
pip install -r requirements.txt


5. 创建必要目录
bash
mkdir -p logs db backups
chmod 700 logs db backups # Linux/Mac



### 3. 获取 API 凭证

1. Telegram API 凭证
- 访问 https://my.telegram.org/auth
- 登录您的 Telegram 账号
- 点击 "API development tools"
- 创建新应用获取:
  - api_id
  - api_hash

2. Bot Token
- 在 Telegram 中找到 @BotFather
- 发送 /newbot 命令
- 按提示设置 bot 名称和用户名
- 获取 bot_token

### 4. 配置

1. 复制配置模板
bash
cp config.yml.default 
chmod 600 config.yml # Linux/Mac


2. 编辑配置文件
bash
nano config.yml.default


3. 填写配置信息
yaml
账户配置
account:
# 监听频道信息的账户
api_id: 'YOUR_API_ID'

api_hash: 'YOUR_API_HASH'

phone: '+86190000010'

username: 'your_username'

# 发送消息的bot token

bot_token: 'BOT_TOKEN'

bot_username: 'your_bot_username'

代理配置（如需要）

proxy:
type: SOCKS5
address: null
port: null


### 5. 运行

#### 方式一：直接运行（开发环境）
bash
python main.py


#### 方式二：Systemd 服务（生产环境推荐）
1. 创建服务文件
bash
sudo nano /etc/systemd/system/keyword-bot.service

2. 添加服务配置
ini
[Unit]
Description=Telegram Keyword Bot
After=network.target
[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/telegram-keyword-bot
Environment=PYTHONPATH=/path/to/telegram-keyword-bot
ExecStart=/path/to/venv/bin/python main.py
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target


3. 启动服务
bash
sudo systemctl daemon-reload
sudo systemctl enable keyword-bot
sudo systemctl start keyword-bot


#### 方式三：Docker 部署
bash
构建并启动
docker-compose up -d
查看日志
docker-compose logs -f


### 6. 首次运行

1. 请输入 Telegram 发送的验证码: 12345


2. 如果开启了两步验证，输入密码


3. 验证成功后显示
Telegram API 验证成功
机器人启动成功


## 使用说明

### 基础命令
- `/订阅 <关键词>` - 添加新的关键词订阅
- `/订阅正则 <正则表达式>` - 添加正则表达式订阅
- `/取消订阅 <关键词>` - 删除关键词订阅
- `/我的订阅` - 查看当前订阅列表
- `/帮助` - 显示帮助信息

### 高级命令
- `/设置模板` - 自定义通知模板
- `/设置格式` - 设置消息格式化选项
- `/设置延迟` - 设置消息延迟发送
- `/设置聚合` - 配置消息聚合
- `/设置转发` - 设置消息转发规则

### 管理命令
- `/黑名单` - 管理黑名单
- `/设置权限` - 设置用户权限
- `/性能统计` - 查看性能指标
- `/健康状态` - 查看系统状态
- `/操作日志` - 查看操作记录

## 常见问题

### 1. 验证码问题
- 确保手机号格式正确（包含国际区号）
- 确保 Telegram 客户端已登录
- 检查是否收到验证码短信

### 2. 网络连接问题
- 检查网络连接
- 确认代理配置正确
- 验证 API 凭证是否正确

### 3. 权限问题
- 检查目录权限
- 确保用户有足够权限
- 验证配置文件权限

## 更新日志

查看 [CHANGELOG.md](CHANGELOG.md) 了解版本更新历史。

## 贡献指南

1. Fork 本仓库
2. 创建新分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 许可证

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE) 文件。

## 联系方式

- 作者：记忆匪浅
- 邮箱：a3361150770@gmail.com

