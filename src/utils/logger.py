from astrbot.api import logger as astrbot_logger

from ..shared.trace_context import TraceContext


class PluginLogger:
    """
    Proxy logger thống nhất ở cấp plugin.

    Tự động thêm tiền tố ``[Phân tích nhóm]`` để dễ nhận diện log của plugin
    trong luồng log hỗn hợp của AstrBot. Không kế thừa trực tiếp
    ``logging.LoggerAdapter`` để phù hợp quy chuẩn framework.
    """

    def __init__(self, prefix: str = "[Phân tích nhóm]"):
        self.prefix = prefix

    def _format_msg(self, msg: str) -> str:
        trace_id = TraceContext.get()
        if trace_id:
            return f"[{trace_id}] {self.prefix} {msg}"
        return f"{self.prefix} {msg}"

    def info(self, msg: str, *args, **kwargs):
        astrbot_logger.info(self._format_msg(msg), *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        astrbot_logger.error(self._format_msg(msg), *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        astrbot_logger.warning(self._format_msg(msg), *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        astrbot_logger.debug(self._format_msg(msg), *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        astrbot_logger.critical(self._format_msg(msg), *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        astrbot_logger.exception(self._format_msg(msg), *args, **kwargs)


# Export logger có tiền tố.
logger = PluginLogger()
