# 台灣法規查詢 MCP Server（教學範例）

資料來源：法務部全國法規資料庫（law.moj.gov.tw）

## 這是什麼

一個可以部署到雲端、給 claude.ai 網頁版 Custom Connector 連接的 MCP Server。
連上後，Claude 在對話中可以呼叫以下 3 個工具：

- `list_supported_laws`：列出目前認得的法規清單（約 45 部常用法規）
- `get_law_article(law_name, article_no)`：查單一條文
- `get_law_full_text(law_name)`：查整部法規全文

## 本機測試

```bash
pip install -r requirements.txt
python3 server.py
```

啟動後會監聽 `http://0.0.0.0:8000/mcp`。

## 部署到 Render（免費方案，新手推薦）

1. 到 GitHub 建立一個新的 repository，把這個資料夾裡的 3 個檔案
   （`server.py`、`requirements.txt`、`README.md`）上傳上去。
2. 到 https://render.com 註冊帳號（可以用 GitHub 帳號登入）。
3. 點 **New +** → **Web Service**。
4. 選擇剛剛建立的 GitHub repository，授權 Render 存取。
5. 設定畫面：
   - **Name**：自己取一個名字，例如 `taiwan-law-mcp`
   - **Runtime**：Python 3
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`python3 server.py`
   - **Instance Type**：Free 即可
6. 點 **Create Web Service**，等待幾分鐘完成部署。
7. 部署完成後，Render 會給你一個網址，例如：
   `https://taiwan-law-mcp.onrender.com`
8. 你的 MCP Server URL 就是：
   `https://taiwan-law-mcp.onrender.com/mcp`
   （注意最後要加 `/mcp`，這是程式裡設定的路徑）

## 接到 claude.ai

1. 到 claude.ai → Customize → Connectors → Add → Add custom connector
2. Name 填：`台灣法規查詢`
3. Remote MCP server URL 填：`https://taiwan-law-mcp.onrender.com/mcp`
4. 按 Continue，不需要 OAuth（這個範例沒有設驗證機制）

## 已知限制（請務必了解）

- **免費方案會休眠**：Render 免費方案閒置一段時間後會睡眠，下次呼叫時
  需要幾十秒喚醒，第一次查詢可能會等比較久甚至逾時，屬正常現象。
- **法規涵蓋範圍有限**：只內建約 45 部最常用的法規代碼（pcode）。
  查詢清單外的法規會回傳「查不到」，不會亂編內容。
- **沒有驗證機制**：任何知道這個網址的人都能呼叫。因為只是唯讀查詢
  公開法規資料，風險低，但如果之後想加驗證，可以在
  `Add custom connector` 視窗展開 Advanced settings 設定 OAuth。
- **依賴網站 HTML 結構**：law.moj.gov.tw 如果改版，解析邏輯
  （`_parse_single_article`、`_parse_law_all` 這兩個函式）可能需要跟著調整。

## 如果想涵蓋全部 11,000+ 部法規

這個範例的 `PCODE_MAP` 是手動列出常用的~45部。如果要擴充到全部法規，
需要先呼叫官方 Open API 的 `https://law.moj.gov.tw/api/Ch/Law/JSON`
下載完整法規清單，從中整理出「法規名稱 → pcode」的完整對照表，
存成一個大字典取代目前的 `PCODE_MAP`。這部分資料量較大，建議另外
寫一支背景排程（例如每週跑一次）定期更新，而不是每次查詢都重新下載。
