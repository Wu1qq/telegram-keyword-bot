import asyncio
from typing import Callable, Any
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class TaskQueue:
    def __init__(self, max_size: int = 1000):
        self.queue = asyncio.Queue(maxsize=max_size)
        self.processing = False
        self.stats = {
            'total_tasks': 0,
            'processed_tasks': 0,
            'failed_tasks': 0,
            'processing_times': []
        }
        
    async def put(self, task: Callable, *args, **kwargs):
        """添加任务到队列"""
        await self.queue.put((task, args, kwargs))
        self.stats['total_tasks'] += 1
        
    async def process_tasks(self):
        """处理队列中的任务"""
        self.processing = True
        while self.processing:
            try:
                if self.queue.empty():
                    await asyncio.sleep(0.1)
                    continue
                    
                task, args, kwargs = await self.queue.get()
                start_time = datetime.now()
                
                try:
                    await task(*args, **kwargs)
                    self.stats['processed_tasks'] += 1
                except Exception as e:
                    self.stats['failed_tasks'] += 1
                    logger.error(f"任务处理失败: {str(e)}")
                finally:
                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.stats['processing_times'].append(processing_time)
                    # 保持最近1000条处理时间记录
                    if len(self.stats['processing_times']) > 1000:
                        self.stats['processing_times'] = self.stats['processing_times'][-1000:]
                    self.queue.task_done()
                    
            except Exception as e:
                logger.error(f"队列处理器错误: {str(e)}")
                await asyncio.sleep(1)
                
    def stop(self):
        """停止任务处理"""
        self.processing = False
        
    def get_stats(self) -> dict:
        """获取任务统计信息"""
        stats = self.stats.copy()
        if self.stats['processing_times']:
            stats['avg_processing_time'] = sum(self.stats['processing_times']) / len(self.stats['processing_times'])
        else:
            stats['avg_processing_time'] = 0
        stats['queue_size'] = self.queue.qsize()
        return stats 