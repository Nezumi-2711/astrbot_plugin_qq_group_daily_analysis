import asyncio
import time

from .logger import logger


class CircuitBreaker:
    """
    Circuit breaker cho khả năng phục hồi hệ thống.

    Theo dõi lời gọi dịch vụ ngoài như API LLM. Khi lỗi đạt ngưỡng, tự mở
    mạch để chặn yêu cầu tiếp theo và tránh lỗi dây chuyền cho đến khi hồi phục.

    States:
        CLOSED: Hoạt động bình thường, cho phép yêu cầu.
        OPEN: Mạch mở, từ chối yêu cầu.
        HALF_OPEN: Thử hồi phục, cho phép một số yêu cầu kiểm tra.
    """

    STATE_CLOSED = "CLOSED"
    STATE_OPEN = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        name: str = "default",
    ):
        """
        Khởi tạo circuit breaker.

        Args:
            failure_threshold: Số lỗi liên tiếp để mở mạch.
            recovery_timeout: Thời gian chờ trước khi thử hồi phục, tính bằng giây.
            name: Định danh circuit breaker dùng trong log.
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self.failure_count = 0
        self.state = self.STATE_CLOSED
        self.last_failure_time = 0.0

    def record_failure(self) -> None:
        """Ghi nhận một lỗi và mở mạch nếu đạt ngưỡng."""
        self.failure_count += 1
        if (
            self.state == self.STATE_CLOSED
            and self.failure_count >= self.failure_threshold
        ):
            self._open_circuit()
        elif self.state == self.STATE_HALF_OPEN:
            # Một lỗi trong trạng thái half-open sẽ mở mạch ngay.
            self._open_circuit()

    def record_success(self) -> None:
        """Ghi nhận thành công và thử reset hoặc đóng mạch."""
        if self.state == self.STATE_HALF_OPEN:
            self._close_circuit()
        elif self.state == self.STATE_CLOSED:
            # Thành công khi mạch đóng sẽ reset bộ đếm lỗi.
            self.failure_count = 0

    def allow_request(self) -> bool:
        """
        Kiểm tra có cho phép yêu cầu dịch vụ này hay không.

        Returns:
            True nếu cho phép, False nếu chặn.
        """
        if self.state == self.STATE_OPEN:
            # Chuyển sang half-open nếu đã hết thời gian chờ.
            if time.monotonic() - self.last_failure_time > self.recovery_timeout:
                self._half_open_circuit()
                return True
            return False
        return True

    def _open_circuit(self) -> None:
        """Mở circuit breaker."""
        self.state = self.STATE_OPEN
        self.last_failure_time = time.monotonic()
        logger.warning(
            f"CircuitBreaker[{self.name}] đã mở; chặn yêu cầu trong {self.recovery_timeout} giây."
        )

    def _close_circuit(self) -> None:
        """Đóng circuit breaker và trở về trạng thái bình thường."""
        self.state = self.STATE_CLOSED
        self.failure_count = 0
        logger.info(f"CircuitBreaker[{self.name}] đã trở về trạng thái CLOSED.")

    def _half_open_circuit(self) -> None:
        """Chuyển circuit breaker sang trạng thái half-open."""
        self.state = self.STATE_HALF_OPEN
        logger.info(f"CircuitBreaker[{self.name}] đã vào chế độ kiểm tra HALF_OPEN.")


class GlobalRateLimiter:
    """
    Bộ giới hạn đồng thời động toàn cục.

    Quản lý ``asyncio.Semaphore`` theo singleton để tác vụ bất đồng bộ không
    vượt giới hạn, giúp kiểm soát chi phí LLM và tránh nghẽn API.
    """

    _instance: "GlobalRateLimiter | None" = None
    _semaphore: asyncio.Semaphore | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls, max_concurrency: int | None = None) -> "GlobalRateLimiter":
        """
        Lấy hoặc tạo singleton rate limiter.

        Args:
            max_concurrency: Số tác vụ đồng thời tối đa; thay đổi sẽ reset semaphore.

        Returns:
            Instance GlobalRateLimiter duy nhất.
        """
        instance = cls()
        if max_concurrency is not None:
            instance.reconfigure(max_concurrency)
        elif cls._semaphore is None:
            # Giá trị fallback mặc định.
            cls._semaphore = asyncio.Semaphore(3)
        return instance

    def reconfigure(self, max_concurrency: int):
        """Cấu hình lại giới hạn đồng thời và thay thế semaphore."""
        if self._semaphore is None or (
            hasattr(self._semaphore, "_value")
            and self._semaphore._value != max_concurrency  # type: ignore
        ):
            old_val = (
                getattr(self._semaphore, "_value", "None")
                if self._semaphore
                else "None"
            )
            logger.info(
                f"GlobalRateLimiter đổi giới hạn đồng thời: {old_val} -> {max_concurrency}"
            )
            self.__class__._semaphore = asyncio.Semaphore(max_concurrency)

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """Trả về semaphore bất đồng bộ cốt lõi."""
        if self._semaphore is None:
            self.__class__._semaphore = asyncio.Semaphore(3)
        assert self._semaphore is not None
        return self._semaphore
