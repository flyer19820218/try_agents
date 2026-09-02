"""Daily major-finance notes plus a Sunday macro report for Taiwan investors.

Monday through Saturday the job selects one material macro-finance headline from
public RSS feeds. Sunday uses FRED and long-horizon market data for the full
macro report. Both formats keep fact, interpretation, and falsification
conditions separate; source data remains available if Gemini is unavailable.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests
import yfinance as yf
import edge_tts
from google import genai
from google.genai import types


TW_TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_FILE = DATA_DIR / "latest_report.json"
HISTORY_DIR = DATA_DIR / "history"
AUDIO_DIR = DATA_DIR / "audio"
TELEGRAM_SAFE_LENGTH = 3800
MIN_REPORT_CHARACTERS = 1000
DAILY_MIN_REPORT_CHARACTERS = 450
REQUIRED_REPORT_SECTIONS = (
    "🧭 【本期核心判讀】",
    "🧾 【四個儀表：事實、判讀、推翻條件】",
    "📈 【趨勢是否互相支持】",
    "🇹🇼 【台灣傳導：事實與驗證】",
    "⚖️ 【資料不能告訴我們什麼】",
    "🔎 【未來一季追蹤清單】",
    "🏫 【曉臻財經小教室】",
)
DAILY_REQUIRED_REPORT_SECTIONS = (
    "🎯 【為何選這一則】",
    "🧭 【暫時判讀】",
    "⚖️ 【不能推出什麼】",
    "🔎 【接下來追蹤】",
    "🏫 【曉臻財經小教室】",
)
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
NARRATION_VOICE = "zh-TW-HsiaoChenNeural"
NARRATION_RELEASE_TAG = "xiaozhen-narration"
NARRATION_TIMEOUT_SECONDS = 120

# Google News RSS offers source links and headline-level evidence without a paid
# news API. The queries deliberately exclude individual earnings and daily stock moves.
NEWS_RSS_QUERIES = (
    ("全球總經", "中央銀行 OR 通膨 OR 就業 OR GDP OR 殖利率 OR 匯率 when:2d"),
    ("台灣外部環境", "台灣 出口 OR 關稅 OR 貿易 OR 能源 OR 油價 when:2d"),
)
MAJOR_NEWS_KEYWORDS = (
    "central bank", "fed", "ecb", "boj", "inflation", "cpi", "jobs", "employment", "gdp",
    "yield", "bond", "currency", "dollar", "tariff", "trade", "export", "energy", "oil",
    "金融", "央行", "通膨", "就業", "失業", "國內生產", "殖利率", "匯率", "關稅", "貿易", "出口", "能源", "油價",
)
NEWS_EXCLUSION_KEYWORDS = (
    "earnings", "revenue", "shares", "stock rises", "stock falls", "price target", "財報", "營收", "股價", "目標價",
    "盤前", "盤後", "學堂", "etf", "概念股", "市場解讀", "technical analysis",
)
TRUSTED_NEWS_SOURCE_KEYWORDS = (
    "reuters", "路透", "associated press", "ap news", "bloomberg", "financial times", "wall street journal",
    "cnbc", "nikkei", "bbc", "the economist", "中央社", "經濟日報", "工商時報", "天下雜誌",
    "udn", "moneydj", "yahoo finance", "鉅亨", "investing.com",
)
PREFERRED_NEWS_SOURCE_KEYWORDS = (
    "reuters", "路透", "associated press", "ap news", "bloomberg", "financial times", "wall street journal",
    "nikkei", "bbc", "the economist", "中央社", "經濟日報", "工商時報", "udn",
)

FINANCE_TERMS = [
    "景氣循環", "通膨", "實質利率", "殖利率曲線", "金融條件", "失業率",
    "工業生產", "貨幣政策", "軟著陸", "停滯性通膨", "資產配置", "再平衡",
]

# Public FRED CSV endpoints need no API key. They provide economic releases,
# rather than commentary about releases, which is exactly what this report uses.
FRED_SERIES = [
    ("CPIAUCSL", "美國 CPI 年增率", "%", "yoy"),
    ("CPILFESL", "美國核心 CPI 年增率", "%", "yoy"),
    ("UNRATE", "美國失業率", "%", "level_3m_change"),
    ("INDPRO", "美國工業生產年增率", "%", "yoy"),
    ("FEDFUNDS", "聯邦基金利率", "%", "level_3m_change"),
    ("T10Y2Y", "美債 10Y−2Y 利差", "百分點", "level_3m_change"),
]

# These are broad-market context series. The report only receives 3/6/12
# month changes: it is never fed an intraday quote or individual stock price.
MARKET_SERIES = [
    ("^TWII", "台灣加權指數", "市場"),
    ("^GSPC", "S&P 500", "市場"),
    ("TWD=X", "美元／台幣", "匯率"),
    ("DX-Y.NYB", "美元指數 DXY", "金融條件"),
    ("^TNX", "美國 10 年期公債殖利率", "利率"),
]


def load_local_env() -> None:
    """Load only this project's optional local Gemini settings.

    GitHub Actions supplies its secret through the environment, which takes
    precedence. Keeping this tiny parser avoids adding another dependency just
    to make a local one-file run convenient.
    """
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key == "GEMINI_API_KEY" and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"').strip("'")


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def save_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def narration_intro(report_type: str) -> str:
    if report_type == "weekly_macro":
        return "曉臻老師，為你導讀本週長線總體經濟觀察。"
    return "曉臻老師，為你導讀今天的一則重大財經新聞。"


def narration_script(report: str, report_type: str) -> str:
    """Turn the Markdown report into a natural script without reading URLs aloud."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", report)
    text = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*#>`_]", "", text)
    text = re.sub(r"[【】]", "。", text)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return f"{narration_intro(report_type)}\n{text.strip()}"


def synthesize_narration(report: str, report_type: str) -> tuple[bytes | None, str | None]:
    """Create a complete narration outside the web request path.

    Failure is deliberately non-fatal: the evidence report must still publish if
    Microsoft's public voice endpoint is temporarily unavailable.
    """
    script = narration_script(report, report_type)
    if not script.strip():
        return None, "導讀稿為空白"

    async def build_audio() -> bytes:
        communicate = edge_tts.Communicate(
            script,
            NARRATION_VOICE,
            rate="+5%",
        )
        chunks = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.extend(chunk["data"])
        return bytes(chunks)

    try:
        audio = asyncio.run(asyncio.wait_for(build_audio(), timeout=NARRATION_TIMEOUT_SECONDS))
    except Exception as error:
        return None, f"語音生成失敗：{type(error).__name__}"
    if not audio:
        return None, "語音服務未回傳音檔"
    return audio, None


def narration_public_url(asset_name: str, generated_at: datetime) -> str | None:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repository:
        return None
    version = generated_at.strftime("%Y%m%d%H%M%S")
    return (
        f"https://github.com/{repository}/releases/download/"
        f"{NARRATION_RELEASE_TAG}/{asset_name}?v={version}"
    )


def create_narration(report: str, report_type: str, generated_at: datetime) -> dict[str, Any]:
    """Save an ignored MP3 for release upload and return safe player metadata."""
    kind = "weekly-macro" if report_type == "weekly_macro" else "daily-major-news"
    asset_name = f"xiaozhen-{kind}-{generated_at:%Y-%m-%d}.mp3"
    audio, error = synthesize_narration(report, report_type)
    if not audio:
        return {
            "status": "unavailable",
            "voice": NARRATION_VOICE,
            "release_tag": NARRATION_RELEASE_TAG,
            "asset_name": asset_name,
            "error": error,
        }
    local_path = AUDIO_DIR / asset_name
    save_bytes(local_path, audio)
    return {
        "status": "ready",
        "voice": NARRATION_VOICE,
        "release_tag": NARRATION_RELEASE_TAG,
        "asset_name": asset_name,
        "local_path": str(local_path.relative_to(ROOT)),
        "public_url": narration_public_url(asset_name, generated_at),
    }


def latest_on_or_before(rows: list[tuple[date, float]], target: date) -> tuple[date, float] | None:
    matches = [row for row in rows if row[0] <= target]
    return matches[-1] if matches else None


def annual_change(rows: list[tuple[date, float]], as_of: date | None = None) -> float | None:
    relevant_rows = [row for row in rows if as_of is None or row[0] <= as_of]
    if len(relevant_rows) < 13:
        return None
    latest_date, latest_value = relevant_rows[-1]
    # Monthly series need a reference roughly one calendar year earlier. 330 days
    # can accidentally select the prior August for a July release (only 11 months).
    base = latest_on_or_before(relevant_rows, latest_date - timedelta(days=360))
    if not base or base[1] == 0:
        return None
    return round((latest_value / base[1] - 1) * 100, 2)


def fetch_fred_series(series_id: str, label: str, unit: str, calculation: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": series_id,
        "label": label,
        "unit": unit,
        "source": "FRED",
        "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
        "status": "unavailable",
    }
    try:
        response = requests.get(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", timeout=20
        )
        response.raise_for_status()
        rows: list[tuple[date, float]] = []
        for row in csv.DictReader(io.StringIO(response.text)):
            raw_value = row.get(series_id, "")
            try:
                rows.append((date.fromisoformat(row["observation_date"]), float(raw_value)))
            except (KeyError, TypeError, ValueError):
                continue
        if not rows:
            return result

        latest_date, latest_value = rows[-1]
        result.update({"status": "available", "as_of": latest_date.isoformat(), "latest": round(latest_value, 2)})
        if calculation == "yoy":
            result["display_value"] = annual_change(rows)
            result["display_label"] = "年增率"
            prior_yoy = annual_change(rows, latest_date - timedelta(days=75))
            if result["display_value"] is not None and prior_yoy is not None:
                result["change_3m"] = round(result["display_value"] - prior_yoy, 2)
                result["change_3m_unit"] = "個百分點"
        else:
            prior = latest_on_or_before(rows, latest_date - timedelta(days=75))
            result["display_value"] = round(latest_value, 2)
            result["display_label"] = "最新值"
            if prior:
                result["change_3m"] = round(latest_value - prior[1], 2)
                result["change_3m_unit"] = "個百分點" if unit == "%" else unit
    except requests.RequestException as error:
        result["error"] = str(error)[:160]
    return result


def nearest_close(closes: Any, target: Any) -> float | None:
    """Use the provider's original timezone-aware index for historical lookup."""
    eligible = closes.loc[closes.index <= target]
    if eligible.empty:
        return None
    value = eligible.iloc[-1]
    return float(value) if value else None


def fetch_market_trend(symbol: str, label: str, category: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "symbol": symbol,
        "label": label,
        "category": category,
        "source": "Yahoo Finance",
        "status": "unavailable",
    }
    try:
        # Yahoo Finance accepts stable named ranges such as 1y/2y; "14mo" can
        # silently degrade to a tiny response on some yfinance versions.
        history = yf.Ticker(symbol).history(period="2y", auto_adjust=False)
        closes = history["Close"].dropna()
        if closes.empty:
            return result
        latest_time = closes.index[-1]
        latest = float(closes.iloc[-1])
        result.update({"status": "available", "as_of": latest_time.strftime("%Y-%m-%d"), "latest": round(latest, 2)})
        for name, days in (("3m", 92), ("6m", 184), ("12m", 366)):
            prior = nearest_close(closes, latest_time - timedelta(days=days))
            if prior and prior != 0:
                if category == "利率":
                    result[f"change_{name}_bps"] = round((latest - prior) * 100)
                else:
                    result[f"change_{name}_pct"] = round((latest / prior - 1) * 100, 2)
    except Exception as error:  # yfinance has several provider-specific exception types.
        result["error"] = str(error)[:160]
    return result


def fetch_macro_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {
        "economic_indicators": [fetch_fred_series(*series) for series in FRED_SERIES],
        "market_trends": [fetch_market_trend(*series) for series in MARKET_SERIES],
    }


def clean_rss_text(value: str | None) -> str:
    plain = re.sub(r"<[^>]+>", " ", unescape(value or ""))
    return re.sub(r"\s+", " ", plain).strip()


def news_importance_score(title: str, summary: str) -> int:
    text = f"{title} {summary}".lower()
    score = sum(keyword in text for keyword in MAJOR_NEWS_KEYWORDS)
    score -= 3 * sum(keyword in text for keyword in NEWS_EXCLUSION_KEYWORDS)
    return score


def is_trusted_news_source(source: str) -> bool:
    lowered = source.lower()
    return any(keyword in lowered for keyword in TRUSTED_NEWS_SOURCE_KEYWORDS)


def is_preferred_news_source(source: str) -> bool:
    lowered = source.lower()
    return any(keyword in lowered for keyword in PREFERRED_NEWS_SOURCE_KEYWORDS)


def rss_published_time(raw_value: str) -> tuple[str, float]:
    try:
        published = parsedate_to_datetime(raw_value).astimezone(TW_TZ)
        return published.strftime("%Y-%m-%d %H:%M（台灣時間）"), published.timestamp()
    except (TypeError, ValueError, OverflowError):
        return raw_value or "發布時間未提供", 0.0


def fetch_major_news_candidates() -> list[dict[str, Any]]:
    """Collect recent, headline-level candidates without scraping article bodies."""
    candidates: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for category, query in NEWS_RSS_QUERIES:
        url = (
            "https://news.google.com/rss/search?q="
            f"{quote_plus(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError):
            continue

        for node in root.findall("./channel/item"):
            title = clean_rss_text(node.findtext("title"))
            summary = clean_rss_text(node.findtext("description"))
            link = clean_rss_text(node.findtext("link"))
            source_node = node.find("source")
            source = clean_rss_text(source_node.text if source_node is not None else "") or "Google News"
            published_at, published_sort = rss_published_time(clean_rss_text(node.findtext("pubDate")))
            normalized_title = re.sub(r"\W+", "", title.lower())
            if not title or not link or normalized_title in seen_titles or not is_trusted_news_source(source):
                continue
            seen_titles.add(normalized_title)
            score = news_importance_score(title, summary)
            if score <= 0:
                continue
            if is_preferred_news_source(source):
                score += 2
            candidates.append(
                {
                    "title": title,
                    "summary": summary[:500],
                    "source": source,
                    "link": link,
                    "published_at": published_at,
                    "category": category,
                    "importance_score": score,
                    "_published_sort": published_sort,
                }
            )

    candidates.sort(key=lambda item: (item["importance_score"], item["_published_sort"]), reverse=True)
    for index, item in enumerate(candidates[:12], start=1):
        item["id"] = index
        item.pop("_published_sort", None)
    return candidates[:12]


def news_candidates_text(candidates: list[dict[str, Any]]) -> str:
    parts = []
    for item in candidates:
        parts.append(
            f"[{item['id']}] {item['title']}\n"
            f"來源：{item['source']}｜發布：{item['published_at']}｜類別：{item['category']}\n"
            f"摘要：{item['summary'] or '未提供'}\n"
            f"連結：{item['link']}"
        )
    return "\n\n".join(parts)


def indicator_text(indicators: list[dict[str, Any]]) -> str:
    lines = []
    for item in indicators:
        if item["status"] != "available":
            lines.append(f"- {item['label']}：資料不可用")
            continue
        value = item.get("display_value")
        if value is None:
            lines.append(f"- {item['label']}：資料不足以計算；最近原始值 {item['latest']}，資料日 {item['as_of']}")
            continue
        extra = ""
        if item.get("change_3m") is not None:
            extra = f"；約三個月變動 {item['change_3m']:+.2f} {item.get('change_3m_unit', item['unit'])}"
        lines.append(f"- {item['label']}：{value:.2f}{item['unit']}（{item['display_label']}；資料日 {item['as_of']}）{extra}")
    return "\n".join(lines)


def market_trend_text(trends: list[dict[str, Any]]) -> str:
    lines = []
    for item in trends:
        if item["status"] != "available":
            lines.append(f"- {item['label']}：資料不可用")
            continue
        changes = []
        for period in ("3m", "6m", "12m"):
            if item["category"] == "利率":
                value = item.get(f"change_{period}_bps")
                if value is not None:
                    changes.append(f"{period} {value:+.0f}bp")
            else:
                value = item.get(f"change_{period}_pct")
                if value is not None:
                    changes.append(f"{period} {value:+.2f}%")
        lines.append(f"- {item['label']}：最近值 {item['latest']:.2f}，{'；'.join(changes) or '歷史資料不足'}，資料日 {item['as_of']}")
    return "\n".join(lines)


def build_prompt(snapshot: dict[str, list[dict[str, Any]]], today_term: str) -> str:
    now_tw = datetime.now(TW_TZ)
    return f"""
你是以長期資產配置為核心的總體經濟研究編輯。今天是 {now_tw:%Y-%m-%d}（台灣時間）。
請只根據下方提供的經濟時間序列與長週期市場趨勢，撰寫「台灣長線投資人總經觀察」。這是一份可被反駁的研究筆記，不是把資料改寫成好聽的市場評論。

本報告的目的：理解景氣、通膨、貨幣政策、金融條件如何改變未來一季到一年的投資環境；不是解釋單日漲跌，更不是預測個股。

嚴格規則：
1. 不得使用或評論即時新聞、地緣政治標題、個別股票、法人單日買賣超、技術指標、成交量或 VIX。
2. 不得補造資料、經濟數字、政策日期、企業資訊或來源。資料日期比今天早是正常現象，必須保留此限制。
3. 觀察到同向或反向變動，只能寫「一致／不一致」，不能宣稱因果。沒有證據時請明說「目前資料不足」。
   只有資料列明「約三個月變動」時，才能描述該指標近期上升、下降、緩和或惡化；只有單一最新值時，只能描述目前水位，不能自行補出前期趨勢。
4. 美股、匯率與殖利率是市場價格；CPI、失業率、工業生產、聯邦基金利率是發布頻率不同的經濟資料。不可把它們混成同一時間點的即時讀數。
5. 對台灣的影響只能討論傳導機制：出口循環、全球需求、美元與資金成本；不得延伸為特定台灣公司或產業的買賣建議。
6. 禁用空泛結論：「韌性、穩健、緩和、相對中性、顯著、接近央行目標」除非同一句附上資料、日期與比較基準。未提供特定央行的目標指標與目標值時，絕對不可寫「接近央行目標」。
7. 每個「可能、反映、代表、支持」後面，必須清楚寫出它是暫時判讀，並在同段寫出一個可觀察的推翻／驗證條件。不能把相關性包裝成結論。
8. 用平實語氣，不使用「必然、確立、噴發、血洗、抄底、追高」等詞。不能給買進、賣出、目標價或持倉比例。
9. 總長 1,200–2,600 個中文字元，使用繁體中文，避免表格。

請固定使用下列格式：

🧭 【本期核心判讀】
150–220 字，且必須嚴格使用三行：
事實：只列 2–3 個精確數值與資料日期，不加形容詞。
暫時判讀：只給一個當期總經假說，例如「成長尚未明顯轉弱，但資金成本是主要制約」；不得寫成預言。
推翻條件：列 2 個未來一季可驗證、可能推翻上述假說的條件。

🧾 【四個儀表：事實、判讀、推翻條件】
以「成長、通膨、貨幣政策、金融條件」四點各自使用三行：
事實：引用提供的資料、精確數值與日期。
暫時判讀：僅說資料較符合／尚不符合什麼狀態；不可把一項指標當成整體經濟結論。
推翻條件：列一項下一季可觀察的數字方向或資料。

📈 【趨勢是否互相支持】
只討論 3、6、12 個月的變動。選 3 組資料，以「事實／暫時判讀／不能推出什麼」寫出市場價格與經濟資料是相互支持、相互矛盾或資料頻率不同；禁止評論 1 個月變動。

🇹🇼 【台灣傳導：事實與驗證】
分成全球需求、匯率、資金成本三點。每點固定寫「外部事實 → 可能傳導 → 仍待驗證」；只談出口循環、進口成本、美元融資與投資環境，不得講任何公司、產業或交易標的。

⚖️ 【資料不能告訴我們什麼】
列 3 點。要具體指出資料發布落後、頻率不同、相關不等於因果，或缺少何種資料；禁止使用泛泛的風險免責聲明。

🔎 【未來一季追蹤清單】
列 4 個可驗證的總經條件，例如核心通膨趨勢、失業率、工業生產、殖利率曲線、美元／台幣的 3 個月方向。不要寫交易指令。

🏫 【曉臻財經小教室】
今日單字：【{today_term}】
兩句白話說明定義與最常見的錯誤解讀。

結尾：一句不超過 16 字的長線提醒，不需押韻。

【經濟指標：資料來源 FRED】
{indicator_text(snapshot['economic_indicators'])}

【市場長週期趨勢：資料來源 Yahoo Finance】
{market_trend_text(snapshot['market_trends'])}
""".strip()


def build_daily_news_prompt(candidates: list[dict[str, Any]], today_term: str) -> str:
    now_tw = datetime.now(TW_TZ)
    return f"""
你是台灣長線投資人的每日財經研究編輯。今天是 {now_tw:%Y-%m-%d}（台灣時間）。
只能根據下方的 Google News RSS 候選標題、摘要、來源、發布時間與連結，選出一則「今天最值得追蹤」的重大財經新聞。這不是新聞摘要，也不是市場喊盤。

選題標準：優先中央銀行、通膨／就業／GDP、主權利率／匯率、系統性金融壓力、貿易政策、能源供給；新聞必須可能影響跨國資金成本、全球需求或台灣外部環境。
排除：個別公司財報、營收、產品、單日股價、技術分析、名人意見與未附可驗證事實的評論。

嚴格規則：
1. 第一行必須且只能是 `CANDIDATE_ID: N`，N 為下方候選編號。此行會由程式移除，讀者看不到。
2. 不得使用候選資料之外的事實、背景、數字、政策日期或因果。標題和摘要不足以支持時，要直接寫「候選資料未提供」。
3. 「事實」只能轉述候選標題／摘要與來源；「暫時判讀」是可能的傳導，不是結論；「不能推出什麼」要直接反駁最容易被過度解讀的地方。
4. 「重大性」只能判定這則新聞屬於全球需求、資金成本、匯率、貿易或能源的哪個觀察類別；不可寫「連鎖效應、影響全球市場、導致資金流動」等未由候選資料支持的結果。
5. 「接下來追蹤」只能寫候選明示的官方決議／聲明，或同一指標的下一次官方發布；不知道日期時不可自行補日期或會議時程。
6. 禁用「必然、確立、韌性、緩和、顯著、抄底、追高、利多、利空」等空泛或交易性詞彙。不能給買賣、標的、目標價或持倉比例。
5. 總長 450–900 個中文字元，使用繁體中文，不使用表格。

第一行之後固定使用以下格式：

🎯 【為何選這一則】
事實：用 2–3 句轉述候選資料，保留來源與發布時間。
不要寫「重大性」或任何影響範圍的結論；程式已在標題上方標示觀察類別。

🧭 【暫時判讀】
用「若……，可能透過……影響……」寫出一條傳導鏈；最後加一行「驗證條件：」且是未來可觀察的資料或官方資訊。

⚖️ 【不能推出什麼】
列 2 點：這則新聞不等於什麼、不足以判定什麼。不可用空泛風險提示。

🔎 【接下來追蹤】
列 3 項可驗證的後續資訊，例如官方數據、政策原文、後續價格或相關經濟指標；不要寫交易指令。

🏫 【曉臻財經小教室】
今日單字：【{today_term}】
直接寫兩句白話說明定義與最常見的錯誤解讀；不可重複本段指令文字。

結尾：一句不超過 16 字的冷靜提醒。

【候選新聞】
{news_candidates_text(candidates)}
""".strip()


def fallback_report(snapshot: dict[str, list[dict[str, Any]]], reason: str) -> str:
    return (
        "🧭 【長線總經資料快照】\n"
        "本次未取得 AI 文字整理；以下保留原始長週期資料。資料變動不代表因果，也不構成投資建議。\n\n"
        "🌡️ 【經濟指標】\n"
        f"{indicator_text(snapshot['economic_indicators'])}\n\n"
        "📈 【市場長週期趨勢】\n"
        f"{market_trend_text(snapshot['market_trends'])}\n\n"
        f"系統狀態：Gemini 報告未生成（{reason[:180]}）。"
    )


def safe_error_summary(message: str) -> str:
    if message.startswith("模型回傳"):
        return message
    lowered = message.lower()
    if any(token in lowered for token in ("429", "503", "quota", "resource exhausted", "unavailable", "high demand")):
        return "模型暫時繁忙或額度受限"
    if any(token in lowered for token in ("401", "403", "api key", "permission")):
        return "Gemini 金鑰或專案權限無法驗證"
    return "Gemini API 呼叫失敗"


def validate_report(
    text: str,
    required_sections: tuple[str, ...] = REQUIRED_REPORT_SECTIONS,
    minimum_characters: int = MIN_REPORT_CHARACTERS,
) -> str | None:
    """Reject partial completions even when the API returns HTTP success."""
    if not text:
        return "模型回傳空白內容"
    if len(text) < minimum_characters:
        return f"模型回傳內容過短（{len(text)} 字，少於 {minimum_characters} 字）"
    missing_sections = [section for section in required_sections if section not in text]
    if missing_sections:
        return "模型回傳內容缺少固定段落"
    return None


def daily_observation_category(item: dict[str, Any]) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if any(token in text for token in ("central bank", "央行", "inflation", "通膨", "cpi", "yield", "殖利率")):
        return "貨幣政策／資金成本"
    if any(token in text for token in ("currency", "dollar", "匯率", "美元", "euro", "歐元")):
        return "匯率／金融條件"
    if any(token in text for token in ("energy", "oil", "能源", "油價")):
        return "能源／通膨"
    return "全球需求／貿易環境"


def daily_report_header(item: dict[str, Any]) -> str:
    return (
        "📰 【今日一則重大財經新聞】\n"
        f"標題：{item['title']}\n"
        f"來源：{item['source']}｜發布：{item['published_at']}\n"
        f"觀察類別：{daily_observation_category(item)}\n"
        f"原文：[開啟原始報導]({item['link']})"
    )


def extract_daily_draft(
    text: str, candidates: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str]:
    match = re.match(r"^CANDIDATE_ID\s*[:：]\s*(\d+)\s*$", text, flags=re.MULTILINE)
    if not match:
        return None, text.strip()
    selected_id = int(match.group(1))
    selected = next((item for item in candidates if item["id"] == selected_id), None)
    return selected, text[match.end():].strip()


def validate_daily_draft(text: str, candidates: list[dict[str, Any]]) -> str | None:
    selected, body = extract_daily_draft(text, candidates)
    if not selected:
        return "模型回傳未選擇有效候選新聞"
    return validate_report(body, DAILY_REQUIRED_REPORT_SECTIONS, DAILY_MIN_REPORT_CHARACTERS)


def daily_fallback_report(candidates: list[dict[str, Any]], today_term: str, reason: str) -> str:
    if not candidates:
        return (
            "📰 【今日重大財經新聞】\n"
            "今日未取得可驗證的重大財經新聞候選。\n\n"
            "⚖️ 【資料不能告訴我們什麼】\n"
            f"RSS 候選資料不可用（{reason}），因此不以模型補寫新聞。\n\n"
            "🏫 【曉臻財經小教室】\n"
            f"今日單字：【{today_term}】\n"
            "總經名詞需要放回資料時間序列中理解，單一標題不能取代資料。"
        )
    item = candidates[0]
    return (
        f"{daily_report_header(item)}\n\n"
        "🎯 【為何選這一則】\n"
        f"事實：Google News RSS 收錄此標題與摘要，來源為 {item['source']}。\n"
        "這則新聞被列為每日候選，因為它屬於上方的觀察類別；本次未取得 AI 研究整理。\n\n"
        "🧭 【暫時判讀】\n"
        "候選資料不足以建立可靠傳導判讀；應先閱讀原始報導與官方資料。\n"
        "驗證條件：後續官方公告或可追溯經濟數據。\n\n"
        "⚖️ 【不能推出什麼】\n"
        "- 單一標題不等於整體景氣轉折。\n"
        "- 單一新聞不足以形成交易判斷。\n\n"
        "🔎 【接下來追蹤】\n"
        "- 原始報導與官方文件。\n"
        "- 同一議題的後續數據。\n"
        "- 是否出現跨市場的一致反應。\n\n"
        "🏫 【曉臻財經小教室】\n"
        f"今日單字：【{today_term}】\n"
        "總經名詞需要放回資料時間序列中理解，單一標題不能取代資料。\n\n"
        "先求證，再判讀。"
    )


def generation_config() -> types.GenerateContentConfig:
    """Use 2.5 Flash without hidden thinking so output remains complete and stable."""
    return types.GenerateContentConfig(
        max_output_tokens=4096,
        response_mime_type="text/plain",
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )


def generate_report(
    prompt: str, fallback_builder: Any, validator: Any = validate_report
) -> tuple[str, str | None, list[str], str | None]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        reason = "未設定 GEMINI_API_KEY"
        return fallback_builder(reason), None, [], reason

    client = genai.Client(api_key=api_key)
    model_name = DEFAULT_GEMINI_MODEL
    attempted_models = [model_name]
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=generation_config(),
        )
        text = (response.text or "").strip()
        error_message = validator(text)
        if not error_message:
            return text, model_name, attempted_models, None
    except Exception as error:
        error_message = str(error)
    summary = safe_error_summary(error_message)
    print(f"WARN: {model_name} did not generate a report: {summary}")
    return fallback_builder(summary), None, attempted_models, summary


def send_telegram_message(text: str) -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("INFO: Telegram credentials are not configured; skipped notification.")
        return
    safe_text = text if len(text) <= TELEGRAM_SAFE_LENGTH else text[:TELEGRAM_SAFE_LENGTH].rsplit("\n", 1)[0] + "\n\n（完整報告請見網站。）"
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": safe_text},
            timeout=15,
        )
        response.raise_for_status()
        print("INFO: Telegram notification sent.")
    except requests.RequestException as error:
        print(f"WARN: Telegram notification failed: {error}")


def run_macro_report() -> None:
    now_tw = datetime.now(TW_TZ)
    snapshot = fetch_macro_snapshot()
    term_index = (now_tw.date() - date(2024, 1, 1)).days % len(FINANCE_TERMS)
    model_name = DEFAULT_GEMINI_MODEL
    report, model_used, attempted_models, generation_error = generate_report(
        build_prompt(snapshot, FINANCE_TERMS[term_index]),
        lambda reason: fallback_report(snapshot, reason),
    )
    send_telegram_message(report)
    narration = create_narration(report, "weekly_macro", now_tw)

    # Keep legacy keys as harmless empty values so old deployed frontends do not crash.
    payload = {
        "updated_at_utc": now_tw.strftime("%Y-%m-%d %H:%M:%S (TW)"),
        "title": f"台灣長線總經觀察 {now_tw:%Y-%m-%d}",
        "report_type": "weekly_macro",
        "model": model_used or model_name,
        "model_requested": model_name,
        "gemini_requests_this_run": len(attempted_models),
        "generation_status": "generated" if model_used else "snapshot_fallback",
        "generation_error": generation_error,
        "report": report,
        "narration": narration,
        "macro_snapshot": snapshot,
        "news": [],
        "risk_indicators": {"vix": "-", "vix_trend": "", "usd_twd": "-", "usd_trend": ""},
    }
    save_json(OUT_FILE, payload)
    save_json(HISTORY_DIR / f"{now_tw:%Y-%m-%d}_長線總經.json", payload)
    print(f"INFO: saved macro report to {OUT_FILE}")


def run_daily_news_report() -> None:
    now_tw = datetime.now(TW_TZ)
    candidates = fetch_major_news_candidates()
    term_index = (now_tw.date() - date(2024, 1, 1)).days % len(FINANCE_TERMS)
    today_term = FINANCE_TERMS[term_index]
    model_name = DEFAULT_GEMINI_MODEL
    selected_news = candidates[0] if candidates else None

    if candidates:
        draft, model_used, attempted_models, generation_error = generate_report(
            build_daily_news_prompt(candidates, today_term),
            lambda reason: daily_fallback_report(candidates, today_term, reason),
            lambda text: validate_daily_draft(text, candidates),
        )
        if model_used:
            selected_news, body = extract_daily_draft(draft, candidates)
            report = f"{daily_report_header(selected_news)}\n\n{body}"
        else:
            report = draft
    else:
        generation_error = "未取得重大新聞候選"
        attempted_models = []
        model_used = None
        report = daily_fallback_report(candidates, today_term, generation_error)

    send_telegram_message(report)
    narration = create_narration(report, "daily_major_news", now_tw)
    payload = {
        "updated_at_utc": now_tw.strftime("%Y-%m-%d %H:%M:%S (TW)"),
        "title": f"每日重大財經新聞 {now_tw:%Y-%m-%d}",
        "report_type": "daily_major_news",
        "model": model_used or model_name,
        "model_requested": model_name,
        "gemini_requests_this_run": len(attempted_models),
        "generation_status": "generated" if model_used else "news_snapshot_fallback",
        "generation_error": generation_error,
        "report": report,
        "narration": narration,
        "macro_snapshot": {},
        "news": [selected_news] if selected_news else [],
        "risk_indicators": {"vix": "-", "vix_trend": "", "usd_twd": "-", "usd_trend": ""},
    }
    save_json(OUT_FILE, payload)
    save_json(HISTORY_DIR / f"{now_tw:%Y-%m-%d}_每日重大財經.json", payload)
    print(f"INFO: saved daily news report to {OUT_FILE}")


def run_scheduled_report() -> None:
    mode = os.environ.get("REPORT_MODE", "").strip().lower()
    if mode in {"macro", "weekly_macro"}:
        run_macro_report()
    elif mode in {"daily", "daily_major_news"}:
        run_daily_news_report()
    elif datetime.now(TW_TZ).weekday() == 6:  # Sunday in Taiwan
        run_macro_report()
    else:
        run_daily_news_report()


if __name__ == "__main__":
    load_local_env()
    run_scheduled_report()
