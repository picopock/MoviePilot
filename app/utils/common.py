import functools
import threading
import time
from typing import Any, Callable, Optional

from app.log import logger
from app.schemas import ImmediateException, APIRateLimitException, RateLimitExceededException


def retry(ExceptionToCheck: Any,
          tries: int = 3, delay: int = 3, backoff: int = 2, logger: Any = None):
    """
    :param ExceptionToCheck: 需要捕获的异常
    :param tries: 重试次数
    :param delay: 延迟时间
    :param backoff: 延迟倍数
    :param logger: 日志对象
    """

    def deco_retry(f):
        def f_retry(*args, **kwargs):
            mtries, mdelay = tries, delay
            while mtries > 1:
                try:
                    return f(*args, **kwargs)
                except ImmediateException:
                    raise
                except ExceptionToCheck as e:
                    msg = f"{str(e)}, {mdelay} 秒后重试 ..."
                    if logger:
                        logger.warn(msg)
                    else:
                        print(msg)
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= backoff
            return f(*args, **kwargs)

        return f_retry

    return deco_retry


class RateLimiter:
    """
    限流器类，用于处理调用的限流逻辑
    通过增加等待时间逐步减少调用的频率，以避免触发限流
    """

    def __init__(self, base_wait: int = 60, max_wait: int = 600, backoff_factor: int = 1):
        """
        初始化 RateLimiter 实例
        :param base_wait: 基础等待时间（秒），默认值为 60 秒（1 分钟）
        :param max_wait: 最大等待时间（秒），默认值为 600 秒（10 分钟）
        :param backoff_factor: 等待时间的递增倍数，默认值为 1
        """
        self.next_allowed_time = 0
        self.current_wait = base_wait
        self.base_wait = base_wait
        self.max_wait = max_wait
        self.backoff_factor = backoff_factor
        self.lock = threading.Lock()

    def can_call(self) -> bool:
        """
        检查是否可以进行下一次调用
        :return: 如果当前时间超过下一次允许调用的时间，返回 True；否则返回 False
        """
        current_time = time.time()
        with self.lock:
            if current_time >= self.next_allowed_time:
                return True
            logger.warn(f"限流期间，跳过调用：将在 {self.next_allowed_time - current_time:.2f} 秒后允许继续调用")
            return False

    def reset(self):
        """
        重置等待时间
        当调用成功时调用此方法，重置当前等待时间为基础等待时间
        """
        with self.lock:
            if self.next_allowed_time != 0 or self.current_wait > self.base_wait:
                logger.info(f"调用成功，重置限流等待时长，并允许立即调用")
            self.next_allowed_time = 0
            self.current_wait = self.base_wait

    def trigger_limit(self):
        """
        触发限流
        当触发限流异常时调用此方法，增加下一次允许调用的时间并更新当前等待时间
        """
        current_time = time.time()
        with self.lock:
            self.next_allowed_time = current_time + self.current_wait
            logger.warn(f"触发限流：将在 {self.current_wait} 秒后允许继续调用")
            self.current_wait = min(self.current_wait * self.backoff_factor, self.max_wait)


def rate_limit_handler(base_wait: int = 60, max_wait: int = 600, backoff_factor: int = 1,
                       raise_on_limit: bool = True) -> Callable:
    """
    装饰器，用于处理限流逻辑
    :param base_wait: 基础等待时间（秒），默认值为 60 秒（1 分钟）
    :param max_wait: 最大等待时间（秒），默认值为 600 秒（10 分钟）
    :param backoff_factor: 等待时间的递增倍数，默认值为 1
    :param raise_on_limit: 是否在触发限流异常时抛出异常，默认为 True
    :return: 装饰器函数
    """
    rate_limiter = RateLimiter(base_wait, max_wait, backoff_factor)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Optional[Any]:
            if not rate_limiter.can_call():
                if raise_on_limit:
                    raise RateLimitExceededException("调用因限流被跳过")
                return None

            try:
                result = func(*args, **kwargs)
                rate_limiter.reset()  # 调用成功，重置等待时间
                return result
            except APIRateLimitException as e:
                rate_limiter.trigger_limit()
                logger.error(f"触发限流：{str(e)}")
                if raise_on_limit:
                    raise e
                return None

        return wrapper

    return decorator
