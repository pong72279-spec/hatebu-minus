#!/usr/bin/env python3
"""Validate and copy the dependency-free static site into dist/."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def validate_data() -> None:
    with (ROOT / "config.json").open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    with (ROOT / "data" / "entries.json").open(encoding="utf-8") as data_file:
        data = json.load(data_file)
    if not isinstance(config, dict):
        raise ValueError("config.json must contain an object")
    if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
        raise ValueError("data/entries.json must contain an articles array")


def build() -> None:
    validate_data()
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    shutil.copy2(ROOT / "index.html", DIST / "index.html")
    shutil.copytree(ROOT / "assets", DIST / "assets")
    shutil.copytree(ROOT / "data", DIST / "data")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built static site at {DIST}")


if __name__ == "__main__":
    build()
