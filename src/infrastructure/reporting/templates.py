"""Module tải template HTML bên ngoài bằng Jinja2."""

import asyncio
import os
import threading

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ...utils.logger import logger


class HTMLTemplates:
    """Trình quản lý template HTML."""

    def __init__(self, config_manager):
        """Khởi tạo môi trường Jinja2."""
        self.config_manager = config_manager
        # Thiết lập thư mục gốc của template.
        self.base_dir = os.path.join(os.path.dirname(__file__), "templates")
        self.platform_base_dir = os.path.join(
            os.path.dirname(__file__), "platform_templates"
        )
        # Cache môi trường Jinja2 theo template, an toàn đa luồng.
        self._envs = {}
        self._env_lock = threading.Lock()

    def _get_env_sync(self) -> Environment:
        """Lấy đồng bộ môi trường template hiện tại cho asyncio.to_thread."""
        template_name = self.config_manager.get_report_template()

        # Trả cache nếu có; dùng lock để đảm bảo an toàn đa luồng.
        with self._env_lock:
            env = self._envs.get(template_name)
            if env is not None:
                return env

        template_dir = os.path.join(self.base_dir, template_name)
        if not os.path.exists(template_dir):
            logger.warning(
                f"Thư mục template không tồn tại: {template_dir}, chuyển sang scrapbook"
            )
            template_dir = os.path.join(self.base_dir, "scrapbook")

        env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # Double-check locking để tránh tạo trùng môi trường khi tải cao.
        with self._env_lock:
            existing = self._envs.get(template_name)
            if existing is not None:
                return existing
            self._envs[template_name] = env

        return env

    async def _get_env_async(self) -> Environment:
        """Lấy bất đồng bộ môi trường template hiện tại."""
        return await asyncio.to_thread(self._get_env_sync)

    def _get_env(self) -> Environment:
        """Lấy đồng bộ môi trường template hiện tại để tương thích ngược."""
        return self._get_env_sync()

    def _read_template_file_sync(self, filename: str) -> str:
        """Đọc đồng bộ nội dung tệp template."""
        with open(filename, encoding="utf-8") as f:
            return f.read()

    async def get_image_template_async(self) -> str:
        """Lấy bất đồng bộ template HTML báo cáo ảnh dưới dạng chuỗi gốc."""
        try:
            env = await self._get_env_async()
            template = env.get_template("image_template.html")
            if template.filename is None:
                logger.error("Đường dẫn template ảnh rỗng")
                return ""
            return await asyncio.to_thread(
                self._read_template_file_sync, template.filename
            )
        except Exception as e:
            logger.error(f"Tải template ảnh thất bại: {e}")
            return ""

    def get_image_template(self) -> str:
        """Lấy đồng bộ template HTML báo cáo ảnh để tương thích ngược."""
        try:
            env = self._get_env()
            template = env.get_template("image_template.html")
            if template.filename is None:
                logger.error("Đường dẫn template ảnh rỗng")
                return ""
            with open(template.filename, encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Tải template ảnh thất bại: {e}")
            return ""

    def render_template(self, template_name: str, **kwargs) -> str:
        """Render tệp template được chỉ định.

        Args:
            template_name: Tên tệp template.
            **kwargs: Biến truyền cho template.

        Returns:
            Chuỗi HTML đã render.
        """
        try:
            env = self._get_env()
            template = env.get_template(template_name)
            return template.render(**kwargs)
        except Exception as e:
            logger.error(f"Render template {template_name} thất bại: {e}")
            return ""

    def render_platform_template(
        self, platform_name: str, template_name: str, **kwargs
    ) -> str:
        """Render template riêng theo nền tảng, độc lập với theme báo cáo."""
        try:
            template_dir = os.path.join(self.platform_base_dir, platform_name)
            env = Environment(
                loader=FileSystemLoader(template_dir),
                autoescape=select_autoescape(["html", "xml"]),
                trim_blocks=True,
                lstrip_blocks=True,
            )
            return env.get_template(template_name).render(**kwargs)
        except Exception as e:
            logger.error(
                f"Render template nền tảng {platform_name}/{template_name} thất bại: {e}"
            )
            return ""
