import time

import pytest

from src.domain.entities.incremental_state import IncrementalBatch
from src.infrastructure.persistence.incremental_store import IncrementalStore


class FakePlugin:
    def __init__(self, data: dict | None = None):
        self.data = dict(data or {})

    async def get_kv_data(self, key, default=None):
        return self.data.get(key, default)

    async def put_kv_data(self, key, value):
        if value is None:
            self.data.pop(key, None)
        else:
            self.data[key] = value


@pytest.mark.asyncio
async def test_first_access_purges_legacy_batches_and_resets_cursor():
    group_id = "group-1"
    old_batch_id = "old-batch"
    plugin = FakePlugin(
        {
            f"incr_batch_index_{group_id}": [
                {"batch_id": old_batch_id, "timestamp": time.time()}
            ],
            f"incr_batch_{group_id}_{old_batch_id}": {
                "group_id": group_id,
                "batch_id": old_batch_id,
                "topics": [{"topic": "旧话题"}],
            },
            f"incr_last_ts_{group_id}": 123456,
        }
    )
    store = IncrementalStore(plugin)

    batches = await store.query_batches(group_id, 0, time.time() + 1)

    assert batches == []
    assert plugin.data[f"incr_batch_index_{group_id}"] == []
    assert f"incr_batch_{group_id}_{old_batch_id}" not in plugin.data
    assert plugin.data[f"incr_last_ts_{group_id}"] == 0
    assert (
        plugin.data[f"incr_language_migration_{group_id}"]
        == IncrementalStore.LANGUAGE_MIGRATION_VERSION
    )


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_preserves_new_vietnamese_batch():
    group_id = "group-2"
    plugin = FakePlugin()
    store = IncrementalStore(plugin)
    batch = IncrementalBatch(
        group_id=group_id,
        timestamp=time.time(),
        topics=[
            {
                "topic": "Kế hoạch cuối tuần",
                "contributors": ["An"],
                "detail": "Mọi người thống nhất đi dã ngoại.",
            }
        ],
    )

    assert await store.save_batch(batch)
    await store.update_last_analyzed_timestamp(group_id, 999)
    loaded_once = await store.query_batches(group_id, 0, time.time() + 1)
    loaded_twice = await store.query_batches(group_id, 0, time.time() + 1)

    assert [item.batch_id for item in loaded_once] == [batch.batch_id]
    assert [item.batch_id for item in loaded_twice] == [batch.batch_id]
    assert await store.get_last_analyzed_timestamp(group_id) == 999
    assert plugin.data[f"incr_batch_index_{group_id}"] == [
        {"batch_id": batch.batch_id, "timestamp": batch.timestamp}
    ]
