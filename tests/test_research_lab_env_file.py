# -*- coding: utf-8 -*-

import os

from src.research_lab.env_file import load_env_file


def test_load_env_file_sets_missing_values_without_printing(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# comment\nA=1\nB='two'\nBROKEN\n", encoding="utf-8")
    monkeypatch.delenv("A", raising=False)
    monkeypatch.setenv("B", "existing")

    loaded = load_env_file(path)

    assert loaded == {"A": "1"}
    assert os.environ["A"] == "1"
    assert os.environ["B"] == "existing"
