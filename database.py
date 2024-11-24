from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, database_url: str, pool_size: int = 5):
        self.engine = create_async_engine(
            database_url,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800
        )
        self.async_session = sessionmaker(
            self.engine, 
            class_=AsyncSession,
            expire_on_commit=False
        )
        
    @asynccontextmanager
    async def session(self):
        """创建数据库会话的上下文管理器"""
        session = self.async_session()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"数据库操作失败: {str(e)}")
            raise
        finally:
            await session.close()
            
    async def check_connection(self) -> bool:
        """检查数据库连接状态"""
        try:
            async with self.session() as session:
                await session.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"数据库连接检查失败: {str(e)}")
            return False
            
    async def get_pool_status(self) -> dict:
        """获取连接池状态"""
        return {
            'size': self.engine.pool.size(),
            'checkedin': self.engine.pool.checkedin(),
            'checkedout': self.engine.pool.checkedout(),
            'overflow': self.engine.pool.overflow()
        } 