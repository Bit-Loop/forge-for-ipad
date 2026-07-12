"""Canonical SideStore/AltStore source generation."""

from __future__ import annotations

from typing import Any

from .packaging import FORGE_BUNDLE_ID, validate_package_metadata


def generate_source(
    packages: list[dict[str, Any]],
    *,
    source_name: str,
    source_identifier: str,
    subtitle: str,
    description: str,
    icon_url: str,
    website: str,
    tint_color: str = "a96d2b",
) -> dict[str, Any]:
    if not packages:
        raise ValueError("at least one Forge package is required")
    for package in packages:
        validate_package_metadata(package)
    ordered = sorted(packages, key=lambda item: item["build_number"], reverse=True)
    versions = [
        {
            "buildVersion": str(item["build_number"]),
            "date": item["release_date"],
            "downloadURL": item["download_url"],
            "localizedDescription": (
                "Offline recovery Seed with embedded runtime payload. Install the following thin build after materialization."
                if item["kind"] == "seed"
                else "Everyday thin Forge update; reuses verified runtime data already on the iPad."
            ),
            "sha256": item["artifact"]["sha256"],
            "size": item["artifact"]["size"],
            "version": item["marketing_version"],
        }
        for item in ordered
    ]
    return {
        "apps": [
            {
                "bundleIdentifier": FORGE_BUNDLE_ID,
                "developerName": "Bit-Loop",
                "iconURL": icon_url,
                "localizedDescription": description,
                "name": "Forge for iPad",
                "permissions": [],
                "subtitle": subtitle,
                "tintColor": tint_color,
                "versions": versions,
            }
        ],
        "featuredApps": [FORGE_BUNDLE_ID],
        "identifier": source_identifier,
        "name": source_name,
        "news": [],
        "subtitle": subtitle,
        "website": website,
    }
