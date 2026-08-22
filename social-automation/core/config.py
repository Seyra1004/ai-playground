from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


@dataclass
class ContentConfig:
    categories: list
    min_candidates: int
    min_score: int
    pages_min: int
    pages_max: int


@dataclass
class PlatformsConfig:
    instagram: bool
    threads: bool


@dataclass
class PublishingConfig:
    instagram_time: str
    threads_time: str


@dataclass
class AccountConfig:
    account_id: str
    name: str
    enabled: bool
    content: ContentConfig
    platforms: PlatformsConfig
    publishing: PublishingConfig
    brand_config_path: str
    root_dir: str


@dataclass
class BrandConfig:
    name: str
    canvas_width: int
    canvas_height: int
    canvas_ratio: str
    colors: dict
    backgrounds: dict
    typography_family: str
    layout: dict
    raw: dict = field(default_factory=dict)


def _require(d: dict, path: str, container_name: str) -> Any:
    node = d
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"Missing required field '{path}' in {container_name}")
        node = node[key]
    return node


def load_account_config(account_id: str, accounts_root: str = "accounts") -> AccountConfig:
    root_dir = os.path.join(accounts_root, account_id)
    account_path = os.path.join(root_dir, "account.yaml")
    if not os.path.isfile(account_path):
        raise ConfigError(f"account.yaml not found for account '{account_id}' at {account_path}")

    with open(account_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    acc_id = _require(raw, "account.id", account_path)
    if acc_id != account_id:
        raise ConfigError(f"account.id '{acc_id}' does not match requested account '{account_id}'")

    name = _require(raw, "account.name", account_path)
    enabled = _require(raw, "account.enabled", account_path)
    if not isinstance(enabled, bool):
        raise ConfigError("account.enabled must be boolean")

    categories = _require(raw, "content.categories", account_path)
    if not isinstance(categories, list) or not categories:
        raise ConfigError("content.categories must be a non-empty list")

    min_candidates = _require(raw, "content.min_candidates", account_path)
    min_score = _require(raw, "content.min_score", account_path)
    pages_min = _require(raw, "content.pages_min", account_path)
    pages_max = _require(raw, "content.pages_max", account_path)

    for field_name, val in (
        ("min_candidates", min_candidates),
        ("min_score", min_score),
        ("pages_min", pages_min),
        ("pages_max", pages_max),
    ):
        if not isinstance(val, int) or isinstance(val, bool):
            raise ConfigError(f"content.{field_name} must be an integer")

    if not (1 <= pages_min <= pages_max):
        raise ConfigError(f"content.pages_min/pages_max invalid: {pages_min}-{pages_max}")
    if pages_min < 4 or pages_max > 8:
        raise ConfigError("Carousel page count must stay within the product spec range of 4-8")
    if not (0 <= min_score <= 100):
        raise ConfigError("content.min_score must be between 0 and 100")
    if min_candidates < 1:
        raise ConfigError("content.min_candidates must be >= 1")

    instagram_flag = _require(raw, "platforms.instagram", account_path)
    threads_flag = _require(raw, "platforms.threads", account_path)
    if not isinstance(instagram_flag, bool) or not isinstance(threads_flag, bool):
        raise ConfigError("platforms.instagram/threads must be boolean")

    instagram_time = _require(raw, "publishing.instagram_time", account_path)
    threads_time = _require(raw, "publishing.threads_time", account_path)

    brand_config_path = _require(raw, "brand_config", account_path)
    if not os.path.isfile(brand_config_path):
        raise ConfigError(f"brand_config path does not exist: {brand_config_path}")

    return AccountConfig(
        account_id=acc_id,
        name=name,
        enabled=enabled,
        content=ContentConfig(categories, min_candidates, min_score, pages_min, pages_max),
        platforms=PlatformsConfig(instagram_flag, threads_flag),
        publishing=PublishingConfig(instagram_time, threads_time),
        brand_config_path=brand_config_path,
        root_dir=root_dir,
    )


def load_brand_config(brand_config_path: str) -> BrandConfig:
    if not os.path.isfile(brand_config_path):
        raise ConfigError(f"brand.yaml not found at {brand_config_path}")

    with open(brand_config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    name = _require(raw, "brand.name", brand_config_path)
    width = _require(raw, "canvas.width", brand_config_path)
    height = _require(raw, "canvas.height", brand_config_path)
    ratio = _require(raw, "canvas.ratio", brand_config_path)

    if (width, height) != (1080, 1350):
        raise ConfigError(f"canvas must be 1080x1350, got {width}x{height}")
    if ratio != "4:5":
        raise ConfigError(f"canvas.ratio must be '4:5', got '{ratio}'")

    colors = _require(raw, "colors", brand_config_path)
    if not isinstance(colors, dict) or not colors:
        raise ConfigError("colors must be a non-empty mapping")

    backgrounds = _require(raw, "backgrounds", brand_config_path)
    typography_family = _require(raw, "typography.family", brand_config_path)
    if typography_family != "Pretendard":
        raise ConfigError(f"typography.family must be 'Pretendard', got '{typography_family}'")

    layout = _require(raw, "layout", brand_config_path)

    return BrandConfig(
        name=name,
        canvas_width=width,
        canvas_height=height,
        canvas_ratio=ratio,
        colors=colors,
        backgrounds=backgrounds,
        typography_family=typography_family,
        layout=layout,
        raw=raw,
    )
