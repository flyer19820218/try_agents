"""iPhone Safari / Add-to-Home-Screen layout.

The Apple touch icon remains configured in index.html. This file intentionally
stays separate from mapp.py because Safari webviews need stricter width and text
scaling protection.
"""

import asyncio
import base64
import json
import os
import re

import edge_tts
import streamlit as st
import streamlit.components.v1 as components


LATEST_FILE = os.environ.get("REPORT_FILE", "data/latest_report.json")
HISTORY_DIR = "data/history"

# Keep the existing distinct iOS app identity. The home-screen icon is index.html's apple-touch-icon.
st.set_page_config(page_title="長線總經觀察｜iOS", page_icon="📱", layout="wide")
st.markdown(
    """
<style>
footer, header, #MainMenu, [data-testid="stHeader"], [data-testid="stFooter"], [data-testid="stBottom"] { display:none !important; }
html, body, [data-testid="stAppViewContainer"], .main { width:100% !important; max-width:100% !important; overflow-x:hidden !important; background:#ffffff !important; color:#0f172a !important; -webkit-text-size-adjust:100%; }
.block-container { max-width:680px !important; padding:calc(16px + env(safe-area-inset-top)) 14px calc(28px + env(safe-area-inset-bottom)) !important; }
.ios-macro-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:14px 0 20px; }
.ios-macro-card { min-width:0; overflow:hidden; background:#f8fafc; border:1px solid #dbe5f1; border-radius:14px; padding:12px; box-sizing:border-box; }
.ios-label { font-size:13px; line-height:1.35; color:#64748b; min-height:36px; word-break:break-word; }
.ios-value { font-size:22px; line-height:1.1; font-weight:850; margin:6px 0; color:#0f172a; font-variant-numeric:tabular-nums; }
.ios-date { font-size:11px; color:#94a3b8; white-space:normal; }
.ios-note { font-size:13px; color:#64748b; line-height:1.55; }
@media (max-width:350px) { .block-container { padding-left:10px !important; padding-right:10px !important; } .ios-macro-grid { gap:8px; } .ios-macro-card { padding:10px; } .ios-value { font-size:20px; } }
</style>
""",
    unsafe_allow_html=True,
)


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def choose_report():
    mode = st.radio("檢視模式", ["最新總經觀察", "歷史回顧"], horizontal=True, label_visibility="collapsed")
    if mode == "最新總經觀察":
        return load_json(LATEST_FILE)
    try:
        reports = sorted((name for name in os.listdir(HISTORY_DIR) if name.endswith(".json")), reverse=True)
    except OSError:
        reports = []
    if not reports:
        st.info("尚無歷史報告。")
        return None
    name = st.selectbox("選擇報告", reports, format_func=lambda item: item.removesuffix(".json"))
    return load_json(os.path.join(HISTORY_DIR, name))


def macro_cards(data):
    indicators = data.get("macro_snapshot", {}).get("economic_indicators", [])
    parts = []
    for item in indicators:
        value = item.get("display_value")
        if item.get("status") != "available" or value is None:
            continue
        three_month = ""
        if item.get("change_3m") is not None:
            three_month = f" · 3月 {item['change_3m']:+.2f}"
        parts.append(
            f"<div class='ios-macro-card'><div class='ios-label'>{item.get('label', '')}</div>"
            f"<div class='ios-value'>{value:.2f}{item.get('unit', '')}</div>"
            f"<div class='ios-date'>資料日 {item.get('as_of', '—')}{three_month}</div></div>"
        )
    return "<div class='ios-macro-grid'>" + "".join(parts) + "</div>" if parts else ""


@st.cache_data(show_spinner=False)
def generate_audio(text):
    if not text:
        return None
    clean = re.sub(r"[【】#*]", " ", text)
    clean = re.sub(r"[\U00010000-\U0010ffff]", "", clean)
    script = "以下為本週長線總體經濟觀察。" + clean

    async def build_audio():
        communicate = edge_tts.Communicate(script, "zh-TW-HsiaoChenNeural", rate="+5%")
        chunks = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.extend(chunk["data"])
        return bytes(chunks)

    try:
        return asyncio.run(build_audio())
    except Exception:
        return None


data = choose_report()
if not data:
    st.warning("找不到總經資料。請等待下一次週更。")
    st.stop()

st.markdown("<div style='font-size:30px;font-weight:900;line-height:1.2;'>🧭 長線總經觀察</div>", unsafe_allow_html=True)
st.markdown("<div class='ios-note'>iPhone Safari 專版｜保留獨立入口與主畫面 icon｜不收錄即時新聞與短線行情。</div>", unsafe_allow_html=True)
st.caption(f"最後更新：{data.get('updated_at_utc', '—')}｜每週一次 Gemini Flash")

audio = generate_audio(data.get("report", ""))
if audio:
    encoded = base64.b64encode(audio).decode()
    components.html(
        f"<audio controls playsinline preload='none' style='width:100%;height:42px' src='data:audio/mp3;base64,{encoded}'></audio>",
        height=48,
    )

st.markdown("### 🌡️ 本週總經儀表")
cards = macro_cards(data)
if cards:
    st.markdown(cards, unsafe_allow_html=True)
else:
    st.info("這份舊報告尚未含總經資料卡；下一次週更後會自動補上。")

st.markdown("### 🤖 長線判讀")
with st.container(border=True):
    st.markdown(data.get("report", "尚未產生報告。"))

st.markdown("<div class='ios-note'>資料來源：FRED 公開經濟時間序列、Yahoo Finance 長週期資料。經濟數據有發布落後；本內容僅供研究與教育參考，不構成投資建議。</div>", unsafe_allow_html=True)
