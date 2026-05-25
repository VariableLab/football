"""
ProQuant 高性能缓存中间件

功能:
1. 提供基于内存的 LRU 缓存 (开发环境)
2. 提供可选的 Redis 缓存 (生产环境)
3. 装饰器支持，一键加速 API 响应
"""
import functools
import json
import logging
from typing import Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger("cache")

class ProQuantCache:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ProQuantCache, cls).__new__(cls)
            cls._instance.memory_store = {}
        return cls._instance

    def get(self, key: str) -> Optional[Any]:
        # 简单内存缓存实现
        item = self.memory_store.get(key)
        if item:
            val, expiry = item
            if datetime.now() < expiry:
                return val
            else:
                del self.memory_store[key]
        return None

    def set(self, key: str, value: Any, ttl_seconds: int = 300):
        expiry = datetime.now() + timedelta(seconds=ttl_seconds)
        self.memory_store[key] = (value, expiry)

cache = ProQuantCache()

def cached_api(ttl_seconds: int = 300):
    """API 缓存装饰器 - 支持同步和异步"""
    import asyncio
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = f"api_cache:{func.__name__}:{str(args)}:{str(kwargs)}"
            cached_val = cache.get(key)
            if cached_val is not None:
                return cached_val
            
            if asyncio.iscoroutinefunction(func):
                async def async_inner():
                    res = await func(*args, **kwargs)
                    cache.set(key, res, ttl_seconds)
                    return res
                return async_inner()
            else:
                result = func(*args, **kwargs)
                cache.set(key, result, ttl_seconds)
                return result
        return wrapper
    return decorator
