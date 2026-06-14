# -*- coding: utf-8 -*-

from scripts.strategy_lab.worker_once import run_worker_once


def test_worker_once_defers_when_lock_exists(tmp_path):
    lock = tmp_path / "state" / "worker.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text('{"pid": 123, "created_at": 1}', encoding="utf-8")

    out = run_worker_once(tmp_path)

    assert out["status"] == "deferred"
    assert out["reason"] == "worker_already_running"
