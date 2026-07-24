"""
Persistence cho batch phân tích gia tăng theo kiến trúc cửa sổ trượt.

Dùng put_kv_data/get_kv_data của AstrBot để lưu riêng từng batch, hỗ trợ
truy vấn theo cửa sổ thời gian, quản lý index và dọn batch hết hạn.

Thiết kế key KV:
- Index batch: incr_batch_index_{group_id}
- Dữ liệu batch: incr_batch_{group_id}_{batch_id}
- Timestamp tin nhắn phân tích cuối: incr_last_ts_{group_id}
"""

from typing import Any

from ...domain.entities.incremental_state import IncrementalBatch
from ...utils.logger import logger


class IncrementalStore:
    """
    Repository persistence cho batch phân tích gia tăng.

    Trách nhiệm chính: lưu và truy vấn batch, quản lý timestamp loại trùng,
    dọn batch hết hạn và cung cấp số lượng batch cho truy vấn trạng thái.
    """

    # Tiền tố key KV
    INDEX_PREFIX = "incr_batch_index"
    BATCH_PREFIX = "incr_batch"
    LAST_TS_PREFIX = "incr_last_ts"

    def __init__(self, star_instance: Any):
        """
        Khởi tạo repository persistence cho batch.

        Args:
            star_instance: Instance Star dùng để truy cập KV storage.
        """
        self.plugin = star_instance

    # ================================================================
    # Xây dựng key
    # ================================================================

    def _index_key(self, group_id: str) -> str:
        """Tạo key index batch."""
        return f"{self.INDEX_PREFIX}_{group_id}"

    def _batch_key(self, group_id: str, batch_id: str) -> str:
        """Tạo key dữ liệu cho một batch."""
        return f"{self.BATCH_PREFIX}_{group_id}_{batch_id}"

    def _last_ts_key(self, group_id: str) -> str:
        """Tạo key timestamp của tin nhắn phân tích cuối."""
        return f"{self.LAST_TS_PREFIX}_{group_id}"

    # ================================================================
    # Thao tác index batch
    # ================================================================

    async def _get_index(self, group_id: str) -> list[dict]:
        """
        Lấy danh sách index batch của nhóm.

        Args:
            group_id: ID nhóm.

        Returns:
            Danh sách mục index, mỗi mục chứa batch_id và timestamp.
        """
        key = self._index_key(group_id)
        try:
            data = await self.plugin.get_kv_data(key, None)
            if data is None:
                return []
            if isinstance(data, list):
                return data
            logger.warning(
                f"Định dạng dữ liệu index batch bất thường (Key: {key}): {type(data)}"
            )
            return []
        except Exception as e:
            logger.error(f"Đọc index batch thất bại (Key: {key}): {e}", exc_info=True)
            return []

    async def _save_index(self, group_id: str, index: list[dict]) -> None:
        """
        Lưu danh sách index batch.

        Args:
            group_id: ID nhóm.
            index: Danh sách mục index.
        """
        key = self._index_key(group_id)
        try:
            await self.plugin.put_kv_data(key, index)
        except Exception as e:
            logger.error(f"Lưu index batch thất bại (Key: {key}): {e}", exc_info=True)
            raise

    # ================================================================
    # Thao tác dữ liệu batch
    # ================================================================

    async def save_batch(self, batch: IncrementalBatch) -> bool:
        """
        Lưu dữ liệu của một batch và cập nhật index.

        Ghi dữ liệu vào key KV riêng rồi thêm metadata batch vào index.

        Args:
            batch: Batch phân tích gia tăng cần lưu.

        Returns:
            True nếu lưu thành công.
        """
        group_id = batch.group_id
        batch_key = self._batch_key(group_id, batch.batch_id)

        try:
            # 1. Lưu dữ liệu batch
            await self.plugin.put_kv_data(batch_key, batch.to_dict())

            # 2. Cập nhật index
            index = await self._get_index(group_id)
            index.append(
                {
                    "batch_id": batch.batch_id,
                    "timestamp": batch.timestamp,
                }
            )
            await self._save_index(group_id, index)

            logger.debug(
                f"Đã lưu batch {batch.batch_id[:8]}... "
                f"(nhóm {group_id}, tin nhắn={batch.messages_count})"
            )
            return True
        except Exception as e:
            logger.error(
                f"Lưu batch thất bại (nhóm {group_id}, batch {batch.batch_id[:8]}...): {e}",
                exc_info=True,
            )
            return False

    async def query_batches(
        self,
        group_id: str,
        window_start: float,
        window_end: float,
    ) -> list[IncrementalBatch]:
        """
        Truy vấn danh sách batch theo cửa sổ thời gian.

        Lọc batch có timestamp trong ``[window_start, window_end]`` từ index
        rồi tải toàn bộ dữ liệu từng batch.

        Args:
            group_id: ID nhóm.
            window_start: Epoch timestamp bắt đầu cửa sổ.
            window_end: Epoch timestamp kết thúc cửa sổ.

        Returns:
            Danh sách batch trong cửa sổ, tăng dần theo timestamp.
        """
        index = await self._get_index(group_id)

        # Lọc batch trong phạm vi cửa sổ.
        matching_entries = [
            entry
            for entry in index
            if window_start <= entry.get("timestamp", 0) <= window_end
        ]

        # Sắp xếp tăng dần theo timestamp.
        matching_entries.sort(key=lambda x: x.get("timestamp", 0))

        batches: list[IncrementalBatch] = []
        for entry in matching_entries:
            batch_id = entry.get("batch_id", "")
            if not batch_id:
                continue

            batch_key = self._batch_key(group_id, batch_id)
            try:
                data = await self.plugin.get_kv_data(batch_key, None)
                if data is not None:
                    batch = IncrementalBatch.from_dict(data)
                    batches.append(batch)
                else:
                    logger.warning(
                        f"Thiếu dữ liệu batch (nhóm {group_id}, batch {batch_id[:8]}...)"
                    )
            except Exception as e:
                logger.error(
                    f"Tải dữ liệu batch thất bại (nhóm {group_id}, batch {batch_id[:8]}...): {e}",
                    exc_info=True,
                )

        logger.debug(
            f"Hoàn tất truy vấn cửa sổ: nhóm {group_id}, "
            f"cửa sổ [{window_start:.0f}, {window_end:.0f}], "
            f"khớp {len(batches)}/{len(index)} batch"
        )

        return batches

    # ================================================================
    # Timestamp tin nhắn phân tích cuối để loại trùng giữa các batch
    # ================================================================

    async def get_last_analyzed_timestamp(self, group_id: str) -> int:
        """
        Lấy timestamp tin nhắn phân tích cuối của nhóm.

        Dùng để lọc tin nhắn đã phân tích trong chế độ gia tăng.

        Args:
            group_id: ID nhóm.

        Returns:
            Epoch timestamp cuối hoặc 0 nếu chưa có.
        """
        key = self._last_ts_key(group_id)
        try:
            data = await self.plugin.get_kv_data(key, 0)
            return int(data) if data else 0
        except Exception as e:
            logger.error(
                f"Đọc timestamp phân tích cuối thất bại (Key: {key}): {e}",
                exc_info=True,
            )
            return 0

    async def update_last_analyzed_timestamp(
        self, group_id: str, timestamp: int
    ) -> None:
        """
        Cập nhật timestamp tin nhắn phân tích cuối của nhóm.

        Args:
            group_id: ID nhóm.
            timestamp: Epoch timestamp của tin nhắn phân tích cuối.
        """
        key = self._last_ts_key(group_id)
        try:
            await self.plugin.put_kv_data(key, timestamp)
            logger.debug(
                f"Đã cập nhật timestamp phân tích cuối: nhóm {group_id}, ts={timestamp}"
            )
        except Exception as e:
            logger.error(
                f"Cập nhật timestamp phân tích cuối thất bại (Key: {key}): {e}",
                exc_info=True,
            )
            raise

    # ================================================================
    # Dọn batch hết hạn
    # ================================================================

    async def cleanup_old_batches(self, group_id: str, before_timestamp: float) -> int:
        """
        Xoá mọi batch của nhóm cũ hơn timestamp chỉ định.

        Tách mục hết hạn và còn giữ, xoá KV của batch hết hạn rồi ghi lại index.

        Args:
            group_id: ID nhóm.
            before_timestamp: Xoá mọi batch trước timestamp này.

        Returns:
            Số batch đã xoá.
        """
        index = await self._get_index(group_id)
        if not index:
            return 0

        # Tách mục hết hạn và mục cần giữ.
        expired = []
        retained = []
        for entry in index:
            if entry.get("timestamp", 0) < before_timestamp:
                expired.append(entry)
            else:
                retained.append(entry)

        if not expired:
            return 0

        # Xoá dữ liệu batch hết hạn.
        deleted_count = 0
        for entry in expired:
            batch_id = entry.get("batch_id", "")
            if not batch_id:
                continue
            batch_key = self._batch_key(group_id, batch_id)
            try:
                await self.plugin.put_kv_data(batch_key, None)
                deleted_count += 1
            except Exception as e:
                logger.error(
                    f"Xoá batch hết hạn thất bại (nhóm {group_id}, batch {batch_id[:8]}...): {e}",
                    exc_info=True,
                )

        # Cập nhật index, chỉ giữ mục chưa hết hạn.
        await self._save_index(group_id, retained)

        logger.info(
            f"Đã dọn batch hết hạn: nhóm {group_id}, "
            f"xoá {deleted_count}, giữ {len(retained)}"
        )

        return deleted_count

    # ================================================================
    # Truy vấn trạng thái
    # ================================================================

    async def get_batch_count(self, group_id: str) -> int:
        """
        Lấy tổng số batch hiện tại của nhóm.

        Args:
            group_id: ID nhóm.

        Returns:
            Tổng số batch.
        """
        index = await self._get_index(group_id)
        return len(index)

    async def get_all_batch_summaries(self, group_id: str) -> list[dict]:
        """
        Lấy tóm tắt mọi batch của nhóm mà không tải toàn bộ dữ liệu.

        Dùng để hiển thị tổng quan batch trong command trạng thái.

        Args:
            group_id: ID nhóm.

        Returns:
            Danh sách tóm tắt batch tăng dần theo thời gian.
        """
        index = await self._get_index(group_id)
        # Sắp xếp tăng dần theo timestamp.
        index.sort(key=lambda x: x.get("timestamp", 0))
        return index
