"""Shared, non-blocking Xiaozhen narration player for every Streamlit layout."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st


ROOT = Path(__file__).resolve().parent


def local_audio_bytes(narration: dict[str, Any]) -> bytes | None:
    """Read only an agent-created relative audio file; never trust an arbitrary path."""
    relative_path = narration.get("local_path")
    if not isinstance(relative_path, str) or not relative_path:
        return None
    try:
        candidate = (ROOT / relative_path).resolve()
        candidate.relative_to(ROOT)
        return candidate.read_bytes() if candidate.is_file() else None
    except (OSError, ValueError):
        return None


def render_narration(data: dict[str, Any]) -> None:
    """Show a prepared MP3 without ever calling a voice service from the page request."""
    narration = data.get("narration")
    if not isinstance(narration, dict):
        st.caption("🎙️ 曉臻導讀會從下一次自動更新起，隨報告一併提供。")
        return

    status = narration.get("status")
    public_url = narration.get("public_url")
    local_audio = local_audio_bytes(narration)

    with st.container(border=True):
        st.markdown("#### 🎙️ 曉臻導讀")
        st.caption("完整播報本篇內容。通勤時可直接播放；也可開啟 MP3 後在手機上另存。")
        if isinstance(public_url, str) and public_url.startswith("https://github.com/"):
            st.audio(public_url, format="audio/mpeg", autoplay=False)
            st.link_button("開啟 MP3（可離線收聽）", public_url, use_container_width=True)
        elif local_audio:
            st.audio(local_audio, format="audio/mpeg", autoplay=False)
        elif status == "unavailable":
            st.caption("這次報告已正常發布，但語音服務暫時沒有產生音檔；下次更新會再嘗試。")
        else:
            st.caption("導讀音檔準備中，重新整理後再試。")
