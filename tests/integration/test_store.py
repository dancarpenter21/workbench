import asyncio
import threading

import pytest
from workbench_common import Store


async def test_journal_commit_does_not_block_loop_and_survives_cancellation(tmp_path, monkeypatch):
    store = Store("journal", tmp_path)
    store.put("operation", "before")
    started = threading.Event()
    release = threading.Event()
    original = store.put_many

    def delayed(values):
        started.set()
        assert release.wait(timeout=5)
        original(values)

    monkeypatch.setattr(store, "put_many", delayed)
    task = asyncio.create_task(store.aput("operation", "committed"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        # This read and cancellation must be possible while disk work is blocked.
        assert store.get("operation") == "before"
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    store.close()
    reopened = Store("journal", tmp_path)
    assert reopened.get("operation") == "committed"
    reopened.close()
