"""Desktop web layout for daily major-finance notes and Sunday macro reports."""

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

st.set_page_config(page_title="財經觀察", page_icon="📰", layout="wide")
st.markdown(
    """
<style>
:root { color-scheme:light; }
footer, header, #MainMenu, [data-testid="stHeader"], [data-testid="stFooter"], [data-testid="stBottom"] { display:none !important; }
.stApp { background:#f7f9fc; color:#0f172a; }
.block-container { max-width:1080px !important; padding:34px 28px 54px !important; }
.hero { background:linear-gradient(135deg,#0f2a4a,#1e40af); color:#fff; border-radius:22px; padding:30px; margin-bottom:24px; box-shadow:0 14px 34px rgba(15,42,74,.16); }
.hero h1 { margin:0 0 8px; font-size:36px; letter-spacing:-.8px; }.hero p { margin:0; color:#dbeafe; font-size:16px; line-height:1.6; }
.status { margin-top:14px; font-size:13px; color:#bfdbfe; }
.macro-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:12px 0 26px; }
.macro-card { background:#fff; border:1px solid #dbe5f1; border-radius:14px; padding:15px; min-width:0; box-shadow:0 2px 7px rgba(15,23,42,.03); }
.macro-label { color:#64748b; font-size:13px; min-height:34px; line-height:1.35; }.macro-value { color:#0f172a; font-size:27px; font-weight:850; margin:7px 0; font-variant-numeric:tabular-nums; }.macro-date { color:#94a3b8; font-size:12px; }
.trend-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; margin:12px 0 24px; }.trend-card { background:#eff6ff; border:1px solid #bfdbfe; border-radius:12px; padding:13px; min-width:0; }.trend-label { color:#1e40af; font-size:13px; min-height:34px; }.trend-value { color:#0f172a; font-size:17px; font-weight:800; margin:6px 0; }.trend-detail { color:#64748b; font-size:12px; line-height:1.55; }
.method { background:#fffdf4; border-left:5px solid #eab308; border-radius:10px; padding:14px 16px; color:#713f12; font-size:14px; line-height:1.65; margin:18px 0 26px; }
@media (max-width:800px) { .block-container { padding:18px 14px 34px !important; }.hero { padding:22px; }.hero h1 { font-size:30px; }.macro-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }.trend-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
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


def select_report():
    controls, refresh = st.columns([5, 1])
    with controls:
        mode = st.radio("檢視模式", ["最新內容", "歷史回顧"], horizontal=True, label_visibility="collapsed")
    with refresh:
        if st.button("重新整理", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    if mode == "最新內容":
        return load_json(LATEST_FILE)
    try:
        names = sorted((name for name in os.listdir(HISTORY_DIR) if name.endswith(".json")), reverse=True)
    except OSError:
        names = []
    if not names:
        return None
    name = st.selectbox("選擇歷史報告", names, format_func=lambda item: item.removesuffix(".json"))
    return load_json(os.path.join(HISTORY_DIR, name))


def indicator_cards(data):
    cards = []
    for item in data.get("macro_snapshot", {}).get("economic_indicators", []):
        value = item.get("display_value")
        if item.get("status") != "available" or value is None:
            continue
        detail = f"資料日 {item.get('as_of', '—')}"
        if item.get("change_3m") is not None:
            detail += f" · 3月 {item['change_3m']:+.2f}"
        cards.append(
            f"<div class='macro-card'><div class='macro-label'>{item.get('label', '')}</div>"
            f"<div class='macro-value'>{value:.2f}{item.get('unit', '')}</div>"
            f"<div class='macro-date'>{detail}</div></div>"
        )
    return "<div class='macro-grid'>" + "".join(cards) + "</div>" if cards else ""


def trend_cards(data):
    cards = []
    for item in data.get("macro_snapshot", {}).get("market_trends", []):
        if item.get("status") != "available":
            continue
        details = []
        for period in ("3m", "6m", "12m"):
            if item.get("category") == "利率":
                value = item.get(f"change_{period}_bps")
                if value is not None:
                    details.append(f"{period} {value:+.0f}bp")
            else:
                value = item.get(f"change_{period}_pct")
                if value is not None:
                    details.append(f"{period} {value:+.2f}%")
        cards.append(
            f"<div class='trend-card'><div class='trend-label'>{item.get('label', '')}</div>"
            f"<div class='trend-value'>最近值 {item.get('latest', '—')}</div>"
            f"<div class='trend-detail'>{' · '.join(details) or '歷史資料不足'}<br>資料日 {item.get('as_of', '—')}</div></div>"
        )
    return "<div class='trend-grid'>" + "".join(cards) + "</div>" if cards else ""


@st.cache_data(show_spinner=False)
def generate_audio(text, report_type):
    if not text:
        return None
    clean = re.sub(r"[【】#*]", " ", text)
    clean = re.sub(r"[\U00010000-\U0010ffff]", "", clean)
    intro = "以下為本週長線總體經濟觀察。" if report_type == "weekly_macro" else "以下為今日重大財經新聞。"
    script = intro + clean

    async def build_audio():
        communicate = edge_tts.Communicate(script, "zh-TW-HsiaoChenNeural", rate="+5%")
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    try:
        return asyncio.run(asyncio.wait_for(build_audio(), timeout=8))
    except Exception:
        return None


data = select_report()
if not data:
    st.warning("找不到最新報告。請等待下一次更新。")
    st.stop()

is_macro = data.get("report_type") == "weekly_macro"
if is_macro:
    hero_title = "🧭 星期日長線總經觀察"
    hero_text = "景氣 · 通膨 · 利率 · 金融條件 · 台灣傳導<br>只留下能跨週期驗證的資料。"
    cadence = "每週日一篇總經週報"
else:
    hero_title = "📰 每日一則重大財經新聞"
    hero_text = "一則可驗證的重大新聞 · 事實 · 暫時判讀 · 推翻條件<br>排除個股、財報與單日漲跌。"
    cadence = "週一至週六每日一篇｜週日改發總經週報"

st.markdown(
    f"<div class='hero'><h1>{hero_title}</h1>"
    f"<p>{hero_text}</p>"
    f"<div class='status'>最後更新：{data.get('updated_at_utc', '—')}　｜　{cadence}</div></div>",
    unsafe_allow_html=True,
)

audio = generate_audio(data.get("report", ""), data.get("report_type", ""))
if audio:
    encoded = base64.b64encode(audio).decode()
    components.html(f"<audio controls preload='none' style='width:100%;height:42px' src='data:audio/mp3;base64,{encoded}'></audio>", height=48)

if is_macro:
    st.markdown("## 🌡️ 總經儀表")
    cards = indicator_cards(data)
    if cards:
        st.markdown(cards, unsafe_allow_html=True)
    else:
        st.info("這份舊報告尚未含總經資料卡；下一次週更後會自動補上。")

    st.markdown("## 📈 3／6／12 個月市場趨勢")
    trends = trend_cards(data)
    if trends:
        st.markdown(trends, unsafe_allow_html=True)
    st.markdown("<div class='method'><strong>怎麼讀：</strong>經濟數據有發布落後，市場價格也不能單獨證明因果。本頁把兩者並列，是為了找出一致與不一致，而不是追逐單日漲跌。</div>", unsafe_allow_html=True)
    report_heading = "## 🤖 本週總經研究筆記"
    source_note = "資料來源：FRED 公開經濟時間序列、Yahoo Finance 長週期資料。"
else:
    st.markdown("<div class='method'><strong>怎麼讀：</strong>這不是全天新聞整理。每天只保留一則與全球需求、資金成本、匯率、貿易或能源有關的候選事件；事實、判讀與不能推出的結論分開寫。</div>", unsafe_allow_html=True)
    report_heading = "## 🤖 今日新聞研究筆記"
    source_note = "資料來源：Google News RSS 的標題、摘要與原文連結。"

st.markdown(report_heading)
with st.container(border=True):
    st.markdown(data.get("report", "尚未產生報告。"))

st.caption(f"{source_note} 內容僅供研究與教育參考，不構成投資建議。")
