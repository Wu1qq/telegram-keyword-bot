import logging
import sys

def setup_logger():
    logger = logging.getLogger('KeywordBot')
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 文件处理器
    file_handler = logging.FileHandler('bot.log')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger 