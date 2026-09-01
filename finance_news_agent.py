"""Weekly, evidence-calibrated macro report for long-term Taiwan investors.

The job intentionally uses one Gemini Flash call for a complete macro report. It
does not collect or interpret intraday headlines, hot stocks, or short-term
institutional flows. Raw observations are saved even when the model is not
available, so a quota error never destroys the underlying dashboard data.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time as clock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yfinance as yf
from google import genai
from google.genai import types


TW_TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_FILE = DATA_DIR / "latest_report.json"
HISTORY_DIR = DATA_DIR / "history"
TELEGRAM_SAFE_LENGTH = 3800

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
        if key in {"GEMINI_API_KEY", "GEMINI_MODEL"} and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"').strip("'")


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def latest_on_or_before(rows: list[tuple[date, float]], target: date) -> tuple[date, float] | None:
    matches = [row for row in rows if row[0] <= target]
    return matches[-1] if matches else None


def annual_change(rows: list[tuple[date, float]]) -> float | None:
    if len(rows) < 13:
        return None
    latest_date, latest_value = rows[-1]
    # Monthly series need a reference roughly one calendar year earlier. 330 days
    # can accidentally select the prior August for a July release (only 11 months).
    base = latest_on_or_before(rows, latest_date - timedelta(days=360))
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
        else:
            prior = latest_on_or_before(rows, latest_date - timedelta(days=75))
            result["display_value"] = round(latest_value, 2)
            result["display_label"] = "最新值"
            if prior:
                result["change_3m"] = round(latest_value - prior[1], 2)
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
            extra = f"；約三個月變動 {item['change_3m']:+.2f} {item['unit']}"
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
請只根據下方提供的經濟時間序列與長週期市場趨勢，撰寫「台灣長線投資人總經觀察」。

本報告的目的：理解景氣、通膨、貨幣政策、金融條件如何改變未來一季到一年的投資環境；不是解釋單日漲跌，更不是預測個股。

嚴格規則：
1. 不得使用或評論即時新聞、地緣政治標題、個別股票、法人單日買賣超、技術指標、成交量或 VIX。
2. 不得補造資料、經濟數字、政策日期、企業資訊或來源。資料日期比今天早是正常現象，必須保留此限制。
3. 觀察到同向或反向變動，只能寫「一致／不一致」，不能宣稱因果。沒有證據時請明說「目前資料不足」。
4. 美股、匯率與殖利率是市場價格；CPI、失業率、工業生產、聯邦基金利率是發布頻率不同的經濟資料。不可把它們混成同一時間點的即時讀數。
5. 對台灣的影響只能討論傳導機制：出口循環、全球需求、美元與資金成本；不得延伸為特定台灣公司或產業的買賣建議。
6. 用平實語氣，不使用「必然、確立、噴發、血洗、抄底、追高」等詞。不能給買進、賣出、目標價或持倉比例。
7. 總長不超過 3,400 個中文字元，使用繁體中文，避免表格。

請固定使用下列格式：

🧭 【長線總經結論】
120–180 字。先說目前景氣／通膨／利率環境較接近什麼狀態；再說最重要的不確定性。結論必須是「暫時判讀」，不是預言。

🌡️ 【四個總經儀表】
以「成長、通膨、貨幣政策、金融條件」四點分別判讀。每點要引用至少一個提供的資料與日期；若資料不足要明說。

📈 【趨勢，而非日線】
只討論 3、6、12 個月的變動。說明市場價格與經濟資料是否一致或不一致；禁止評論 1 個月變動。

🇹🇼 【對台灣長線配置的傳導】
用 3 點討論全球需求、匯率與資金成本可能怎麼影響台灣的長期環境；每一點必須包含「仍待驗證」條件。

⚖️ 【目前不能下結論的地方】
列 2–3 點資料限制、發布落後或相關不等於因果的提醒。

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


def generate_report(prompt: str, snapshot: dict[str, list[dict[str, Any]]]) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return fallback_report(snapshot, "未設定 GEMINI_API_KEY")

    client = genai.Client(api_key=api_key)
    # Gemini 3.7 Flash is the current stable Flash model. Its default medium
    # thinking level is retained; older sampling controls such as temperature
    # are deliberately omitted because they are not supported by this model.
    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
    config = types.GenerateContentConfig(
        max_output_tokens=2400,
        response_mime_type="text/plain",
    )
    for attempt in range(2):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config=config)
            text = (response.text or "").strip()
            return text if text else fallback_report(snapshot, "模型回傳空白內容")
        except Exception as error:
            message = str(error)
            quota_error = any(token in message.lower() for token in ("429", "quota", "resource exhausted"))
            if attempt == 0 and not quota_error:
                clock.sleep(4)
                continue
            return fallback_report(snapshot, message)
    return fallback_report(snapshot, "未知模型錯誤")


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
    report = generate_report(build_prompt(snapshot, FINANCE_TERMS[term_index]), snapshot)
    send_telegram_message(report)

    # Keep legacy keys as harmless empty values so old deployed frontends do not crash.
    payload = {
        "updated_at_utc": now_tw.strftime("%Y-%m-%d %H:%M:%S (TW)"),
        "title": f"台灣長線總經觀察 {now_tw:%Y-%m-%d}",
        "report_type": "weekly_macro",
        "model": os.environ.get("GEMINI_MODEL", "gemini-3.7-flash"),
        "gemini_requests_this_run": 1 if os.environ.get("GEMINI_API_KEY") else 0,
        "report": report,
        "macro_snapshot": snapshot,
        "news": [],
        "risk_indicators": {"vix": "-", "vix_trend": "", "usd_twd": "-", "usd_trend": ""},
    }
    save_json(OUT_FILE, payload)
    save_json(HISTORY_DIR / f"{now_tw:%Y-%m-%d}_長線總經.json", payload)
    print(f"INFO: saved macro report to {OUT_FILE}")


if __name__ == "__main__":
    load_local_env()
    run_macro_report()
