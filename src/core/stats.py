"""Pure helpers for scan statistics (no Qt imports)."""
from __future__ import annotations

from src.core.models import FileCategory


def stats_key_for_category(category: FileCategory) -> str:
    """Return the dashboard counter key for a file category."""
    return {
        FileCategory.IMAGE: "images",
        FileCategory.VIDEO: "videos",
        FileCategory.AUDIO: "audio",
        FileCategory.DOCUMENT: "documents",
        FileCategory.ARCHIVE: "archives",
        FileCategory.OTHER: "other",
        FileCategory.EXECUTABLE: "other",
        FileCategory.DATABASE: "other",
    }.get(category, "other")
