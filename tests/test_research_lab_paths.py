# -*- coding: utf-8 -*-

import pytest

from src.research_lab.paths import PROJECT_ROOT, resolve_private_root


def test_private_root_rejects_public_repo_path():
    with pytest.raises(ValueError):
        resolve_private_root(PROJECT_ROOT / "reports")


def test_private_root_allows_explicit_public_override():
    public_reports = (PROJECT_ROOT / "reports").resolve()

    assert resolve_private_root(public_reports, allow_public_output=True) == public_reports
