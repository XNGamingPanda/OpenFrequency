from __future__ import annotations

import re
from typing import Any

import requests

from core.version import APP_VERSION, GITHUB_OWNER, GITHUB_REPO


def normalize_version_tag(tag: str) -> str:
    return (tag or "").strip().removeprefix("v").strip()


def _parse_version(value: str) -> tuple[int, int, int, int, int]:
    """
    Parse versions like:
    - v3.9-beta
    - 3.9
    - 3.9.0
    - 3.9.1-alpha2
    """
    text = normalize_version_tag(value).lower()
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-.]?([a-z]+)(\d*)?)?$", text)
    if not match:
        return (0, 0, 0, 0, 0)

    major = int(match.group(1) or 0)
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    label = (match.group(4) or "").strip()
    label_number = int(match.group(5) or 0)

    rank_map = {
        "alpha": 0,
        "beta": 1,
        "rc": 2,
        "preview": 2,
        "pre": 2,
        "": 3,
        "final": 3,
        "release": 3,
    }
    rank = rank_map.get(label, 1)
    return (major, minor, patch, rank, label_number)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    return _parse_version(latest) > _parse_version(current)


def format_version_tag(version: str) -> str:
    text = (version or "").strip()
    return text if text.lower().startswith("v") else f"v{text}"


def get_latest_release(owner: str = GITHUB_OWNER, repo: str = GITHUB_REPO, timeout: int = 10) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{repo}-update-checker/{APP_VERSION}",
        },
    )
    response.raise_for_status()
    return response.json()


def find_preferred_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not assets:
        return None

    priorities = (
        lambda a: str(a.get("name", "")).lower().endswith(".msi"),
        lambda a: str(a.get("name", "")).lower().endswith(".exe"),
        lambda a: str(a.get("name", "")).lower().endswith(".zip"),
    )
    for pick in priorities:
        for asset in assets:
            if pick(asset):
                return asset
    return assets[0]


def build_update_payload(owner: str = GITHUB_OWNER, repo: str = GITHUB_REPO, current_version: str = APP_VERSION) -> dict[str, Any]:
    payload = {
        "current_version": current_version,
        "latest_version": current_version,
        "has_update": False,
        "download_url": None,
        "asset_name": None,
        "release_notes": "",
        "release_url": f"https://github.com/{owner}/{repo}/releases",
        "tag_name": format_version_tag(current_version),
    }

    try:
        data = get_latest_release(owner=owner, repo=repo)
    except Exception as exc:
        payload["error"] = str(exc)
        return payload

    latest_tag = data.get("tag_name", "")
    latest_version = normalize_version_tag(latest_tag)
    asset = find_preferred_asset(data.get("assets", []))

    payload.update({
        "latest_version": latest_version or current_version,
        "release_notes": data.get("body", "") or "",
        "tag_name": latest_tag or format_version_tag(current_version),
        "release_url": data.get("html_url") or payload["release_url"],
        "asset_name": asset.get("name") if asset else None,
        "download_url": asset.get("browser_download_url") if asset else None,
        "has_update": bool(latest_version) and is_newer_version(latest_version, current_version),
    })
    return payload
