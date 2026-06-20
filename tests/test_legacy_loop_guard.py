# -*- coding: utf-8 -*-
"""Phase 0.7 — archived loops must not write to the shared queue without explicit ack."""
from __future__ import annotations

import sys
from argparse import Namespace

import pytest

from scripts.strategy_lab import scanner_farm_loop as SFL
from scripts.strategy_lab import universe_farm_loop as UFL


class TestGuardFunction:
    def test_scanner_aborts_without_ack(self) -> None:
        with pytest.raises(SystemExit) as e:
            SFL._legacy_abort_unless_acknowledged(Namespace(i_understand_legacy=False))
        assert e.value.code == 2

    def test_scanner_ok_with_ack(self) -> None:
        SFL._legacy_abort_unless_acknowledged(Namespace(i_understand_legacy=True))  # no raise

    def test_universe_aborts_without_ack(self) -> None:
        with pytest.raises(SystemExit) as e:
            UFL._legacy_abort_unless_acknowledged(Namespace(i_understand_legacy=False))
        assert e.value.code == 2

    def test_universe_ok_with_ack(self) -> None:
        UFL._legacy_abort_unless_acknowledged(Namespace(i_understand_legacy=True))  # no raise


class TestMainAborts:
    def test_scanner_main_aborts_before_any_work(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sys, "argv", ["scanner_farm_loop", "--once", "--dry-run"])
        with pytest.raises(SystemExit) as e:
            SFL.main()
        assert e.value.code == 2
        assert "ARCHIVE-LEGACY" in capsys.readouterr().out

    def test_universe_main_aborts_before_any_work(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sys, "argv", ["universe_farm_loop", "--once", "--dry-run"])
        with pytest.raises(SystemExit) as e:
            UFL.main()
        assert e.value.code == 2
        assert "ARCHIVE-LEGACY" in capsys.readouterr().out
