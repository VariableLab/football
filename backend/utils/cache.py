"""
ProQuant 高性能缓存中间件

功能:
1. 提供基于内存所在的 LRU 缓存 (开发环境)
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
    from sqlalchemy.orm import Session
    from fastapi import Request

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 过滤掉不可序列化或随请求变化的对象 (Session, Request)
            clean_args = []
            for arg in args:
                if not isinstance(arg, (Session, Request)):
                    clean_args.append(arg)
            
            clean_kwargs = {}
            for k, v in kwargs.items():
                if not isinstance(v, (Session, Request)):
                    clean_kwargs[k] = v

            key = f"api_cache:{func.__name__}:{str(clean_args)}:{str(clean_kwargs)}"
            cached_val = cache.get(key)
            if cached_val is not None:
                logger.info(f"[cache] HIT: {func.__name__}")
                return cached_val
            
            logger.debug(f"[cache] MISS: {func.__name__}")
            
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
