# -*- coding: utf-8 -*-

from scripts.strategy_lab.worker_loop import loop


def test_worker_loop_one_iteration_logs_empty_queue(tmp_path):
    rc = loop(tmp_path / "private", max_iterations=1, sleep_seconds=1, error_sleep_seconds=1)

    log = tmp_path / "private" / "logs" / "worker_loop.log"
    text = log.read_text(encoding="utf-8")
    assert rc == 0
    assert "worker_loop started" in text
    assert "queue=empty" in text
    assert "iteration=1 exit=0" in text

