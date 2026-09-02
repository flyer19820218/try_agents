🚀 Lyu-Science-Cloud Macro Compass | 每日重大財經 × 週日總經

一套以台灣長線投資人為核心的自動化觀察系統。週一至週六每天精選一則可驗證的重大財經新聞；星期日以可追溯的經濟時間序列整理完整總經週報。

💡 Core Philosophy / 產品核心理念
"Investing is a marathon, not a sprint." / 「投資是一場馬拉松，而不是百米衝刺。」

本系統刻意排除即時新聞、熱門股、短線法人籌碼與盤前／盤後評論。它保留的是有發布頻率與資料日期的經濟指標，並把市場資料限制為 3、6、12 個月的趨勢，降低「看新聞就交易」的誘惑。

✨ Key Features / 核心功能
1. 🤖 Evidence-calibrated Macro Analysis（總體經濟觀察）

每日新聞只從央行、通膨、就業、利率、匯率、貿易、能源與系統性金融事件中擇一。系統會先還原 Google News 的原始連結、讀取該篇公開正文，再生成一篇完整的單題研究；星期日才讀取 FRED 與長週期市場資料。兩種報告皆把觀點拆成「事實、暫時判讀、推翻條件」；模型不得把相關寫成因果、不得補造資料，也不得給個股買賣建議。

2. 🎙️ Xiaozhen Full Narration（曉臻完整導讀）
(EN) The scheduled job creates a complete HsiaoChen Neural MP3 after each report, then publishes it as a GitHub Release asset. The web page only plays the prepared file, so readers never wait for text-to-speech during page loading.

(TW) 每份報告完成後，排程會以 edge-tts 的曉臻語音產生完整 MP3，並發布為 GitHub Release 音檔。網頁只播放已備好的音檔，不會在使用者開頁時才即時合成；手機可直接播放，也可開啟 MP3 另存供通勤離線收聽。

3. 📱 Dual-Microservice Architecture (雙端微服務架構)
(EN) Independent routing and UI rendering for Desktop (app.py) and Mobile (mapp.py), featuring customized CSS, responsive grids, and iOS dark-mode fixes.

(TW) 電腦版與手機版獨立架構。平日呈現單一重大新聞研究筆記；星期日才呈現總經儀表與長週期趨勢，並保留 iOS Safari 的專屬顯示保護。

4. ⚙️ 低額度自動化

GitHub Actions 在每週一至週六台灣時間 05:09 發布每日新聞筆記；星期日 05:09 發布完整總經週報。每次只呼叫一次 Gemini 2.5 Flash，並關閉隱藏思考以保留輸出額度；模型失敗時仍會保存原始新聞或總經資料快照。

🏗️ System Architecture / 系統架構
Data Aggregation: Google News RSS 候選新聞與選定來源的公開正文（平日）+ FRED 公開經濟時間序列與 yfinance 長週期市場資料（星期日）。

Data Processing: 1× Gemini 2.5 Flash evidence-calibrated synthesis -> report JSON + an HsiaoChen narration MP3 published outside Git history.

## Gemini model

The project uses only `gemini-2.5-flash`. Copy `.env.example` to `.env` and add
a Gemini API key for local runs; GitHub Actions continues to use the existing
`GEMINI_API_KEY` repository secret. Do not commit `.env`.

Frontend Caching: Advanced Streamlit @st.cache_data implementation to prevent memory leaks (Out of Memory) and handle high-frequency wake-ups.

Hosting Pipeline: GitHub repository connected to Streamlit Community Cloud with CI/CD deployment.
🛠️ Tech Stack / 技術堆疊
Backend & Automation: Python 3.10+, asyncio, requests, pandas, Cron-job.org

Frontend: Streamlit, HTML5/CSS3 Custom Components

AI & Voice: Gemini API, edge-tts

Financial Data: yfinance, Web Scraping (BeautifulSoup / lxml)

🚀 Quick Start / 快速啟動
Clone the repository (複製專案)

Bash
git clone https://github.com/your-username/Lyu-Science-Cloud-APP.git
cd Lyu-Science-Cloud-APP
Install dependencies (安裝套件)

Bash
pip install -r requirements.txt
Run the Application (啟動伺服器)

For Desktop Web (電腦網頁版):

Bash
streamlit run app.py
For Mobile App (手機特務版):

Bash
streamlit run mapp.py
📄 Disclaimer / 免責聲明
(EN) The financial data and AI-generated summaries provided by this application are for informational and educational purposes only. They do not constitute financial advice. Users should conduct their own research before making any investment decisions.

(TW) 本系統提供之財經數據與 AI 摘要僅供學術交流與資訊參考，不構成任何投資建議。使用者在進行任何金融交易前，應自行審慎評估風險。

Built with ❤️ for Long-Term Investors.
