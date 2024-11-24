import yaml
import os

class Config:
    def __init__(self):
        self.config_path = os.getenv('CONFIG_PATH', 'config.yml')
        self.load_config()
    
    def load_config(self):
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            
        # 账户配置
        self.api_id = config['account']['api_id']
        self.api_hash = config['account']['api_hash']
        self.phone = config['account']['phone']
        self.username = config['account']['username']
        self.bot_token = config['account']['bot_token']
        self.bot_username = config['account']['bot_username']
        
        # 代理配置
        self.proxy = None
        if config['proxy']['address'] and config['proxy']['port']:
            self.proxy = {
                'scheme': config['proxy']['type'].lower(),
                'hostname': config['proxy']['address'],
                'port': config['proxy']['port'],
                'username': config['proxy'].get('username'),
                'password': config['proxy'].get('password')
            }
        
        # 其他配置
        self.admin_users = config.get('admin_users', [])
        self.monitor = config.get('monitor', {})
        self.notification = config.get('notification', {})