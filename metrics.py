import psutil
import time
from datetime import datetime
import logging
from typing import Dict, List
import asyncio

logger = logging.getLogger(__name__)

class Metrics:
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self.metrics = {
            'cpu_usage': [],
            'memory_usage': [],
            'disk_io': [],
            'network_io': [],
            'response_times': [],
            'error_counts': {},
            'start_time': datetime.now()
        }
        
    async def collect_metrics(self):
        """收集系统指标"""
        while True:
            try:
                # CPU 使用率
                cpu_percent = psutil.cpu_percent(interval=1)
                self.metrics['cpu_usage'].append({
                    'time': datetime.now(),
                    'value': cpu_percent
                })
                
                # 内存使用
                memory = psutil.Process().memory_info()
                self.metrics['memory_usage'].append({
                    'time': datetime.now(),
                    'rss': memory.rss,
                    'vms': memory.vms
                })
                
                # 磁盘 IO
                disk_io = psutil.disk_io_counters()
                self.metrics['disk_io'].append({
                    'time': datetime.now(),
                    'read_bytes': disk_io.read_bytes,
                    'write_bytes': disk_io.write_bytes
                })
                
                # 网络 IO
                net_io = psutil.net_io_counters()
                self.metrics['network_io'].append({
                    'time': datetime.now(),
                    'bytes_sent': net_io.bytes_sent,
                    'bytes_recv': net_io.bytes_recv
                })
                
                # 清理历史数据
                self._cleanup_metrics()
                
                await asyncio.sleep(60)  # 每分钟收集一次
                
            except Exception as e:
                logger.error(f"指标收集失败: {str(e)}")
                await asyncio.sleep(60)
                
    def record_response_time(self, response_time: float):
        """记录响应时间"""
        self.metrics['response_times'].append({
            'time': datetime.now(),
            'value': response_time
        })
        
    def record_error(self, error_type: str):
        """记录错误"""
        if error_type not in self.metrics['error_counts']:
            self.metrics['error_counts'][error_type] = 0
        self.metrics['error_counts'][error_type] += 1
        
    def _cleanup_metrics(self):
        """清理过期的指标数据"""
        for key in ['cpu_usage', 'memory_usage', 'disk_io', 'network_io', 'response_times']:
            if len(self.metrics[key]) > self.max_history:
                self.metrics[key] = self.metrics[key][-self.max_history:]
                
    def get_metrics(self) -> dict:
        """获取性能指标摘要"""
        current_time = datetime.now()
        uptime = (current_time - self.metrics['start_time']).total_seconds()
        
        # 计算平均响应时间
        avg_response_time = 0
        if self.metrics['response_times']:
            avg_response_time = sum(r['value'] for r in self.metrics['response_times']) / len(self.metrics['response_times'])
            
        # 获取最新的资源使用情况
        latest_memory = self.metrics['memory_usage'][-1] if self.metrics['memory_usage'] else None
        latest_cpu = self.metrics['cpu_usage'][-1] if self.metrics['cpu_usage'] else None
        
        return {
            'uptime': uptime,
            'avg_response_time': avg_response_time,
            'error_counts': self.metrics['error_counts'],
            'current_memory': latest_memory,
            'current_cpu': latest_cpu,
            'total_metrics': {
                'cpu_usage': len(self.metrics['cpu_usage']),
                'memory_usage': len(self.metrics['memory_usage']),
                'response_times': len(self.metrics['response_times'])
            }
        } 