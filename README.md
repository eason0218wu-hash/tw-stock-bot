# 台股盤中選股機器人

盤中（09:00–13:30）每 5 分鐘掃描一次觀察名單，抓出「今日累積成交量」前 30 名，
若其中有股票 **漲幅 > 8.5%** 且 **開盤價 > 5 日均線**，就透過 Telegram 發通知。

全部使用免費資源：
- 資料來源：TWSE 官方即時行情 API（透過 `twstock` 套件呼叫，免金鑰）
- 執行環境：GitHub Actions（免費額度內排程執行）
- 通知方式：Telegram Bot（免費）

## 一、建立 Telegram Bot

1. Telegram 搜尋 `@BotFather` → 傳 `/newbot` → 依指示取得 **Bot Token**
2. 跟你剛建立的 Bot 傳一句話（隨便打）
3. 瀏覽器打開：
   `https://api.telegram.org/bot<你的TOKEN>/getUpdates`
   在回傳的 JSON 裡找到 `"chat":{"id": xxxxxxx}`，這組數字就是 **Chat ID**

## 二、放到 GitHub

1. 建一個新的 GitHub repository，把這個資料夾內容全部上傳（含 `.github/workflows/watch.yml`）
2. 到 repo 的 **Settings → Secrets and variables → Actions → New repository secret**，新增：
   - `TELEGRAM_BOT_TOKEN` = 剛剛拿到的 Bot Token
   - `TELEGRAM_CHAT_ID` = 剛剛拿到的 Chat ID
3. 到 **Settings → Actions → General**，把 Workflow permissions 設為
   「Read and write permissions」（讓程式能把已通知清單寫回 repo，避免重複通知）

## 三、調整觀察名單

- `watchlist.txt` 裡預設放了約 20 檔高流動性股票，可自行增減（一行一個代碼）
- 如果要掃「全市場」，把 `watchlist.txt` 刪掉或清空即可（程式會自動抓全部上市股票，
  但這樣每次掃描要打比較多次 API，跑比較久、也比較容易被 TWSE 限速，建議先用精簡清單測試穩定後再考慮擴大）

## 四、測試

上傳到 GitHub 後，到 repo 的 **Actions** 分頁 → 選 `TW Stock Watch` → **Run workflow**
手動觸發一次，看 log 有沒有正常跑完、Telegram 有沒有收到測試訊息。

> 建議先把 `PCT_THRESHOLD` 在 `main.py` 裡臨時調低（例如改成 0.5），確認整條流程（抓資料 → 判斷 → 發通知）都是通的，
> 測試完再改回 8.5。

## 五、已知限制 / 注意事項

- TWSE 即時行情是公開免費 API，但沒有正式 SLA，欄位格式未來可能微調；若程式報錯，
  把 Actions 的 log 貼給我，我可以協助排查修正。
- GitHub Actions 的排程（cron）不保證準時觸發，尖峰時段可能延遲幾分鐘，這是免費方案的已知限制。
- 這支程式**只負責偵測與通知**，不會下單，進場與否仍需你自己判斷、自己操作。
- 免費 API 抓到的是「近似即時」（約 3–5 秒更新），非交易所主機房等級的低延遲資料，
  用於當沖決策時請自行評估延遲風險。
