"""
台灣法規查詢 MCP Server（簡化教學版）
資料來源：法務部全國法規資料庫 (law.moj.gov.tw)

這個伺服器提供 3 個工具給 Claude 使用：
  1. list_supported_laws  - 列出這個簡化版目前認得的法規名稱
  2. get_law_article      - 查詢某部法規的「單一條文」
  3. get_law_full_text    - 查詢某部法規的「全部條文」

原理：law.moj.gov.tw 網站本身沒有開放「用法規名稱搜尋」的正式 JSON API，
但每一部法規都有一個固定的代碼（pcode），只要知道 pcode，就能組出
真正的查詢網址（跟你在瀏覽器打開全國法規資料庫看到的頁面是同一個），
抓回 HTML 後，用 BeautifulSoup 把條文內容解析出來。

這個範例先內建約 60 部最常用的法規代碼（勞基法、民法、公司法...等），
足夠應付大部分合約審閱情境。如果需要涵蓋全部 11,000+ 部法規，
可以參考文末說明，改用官方 Open API 產生完整對照表。
"""

import re
import logging
from typing import Optional

# ──────────────────────────────────────────────
# SSL 憑證修正（務必放在 import httpx 之前）
#
# law.moj.gov.tw 的憑證鏈結上到「TWCA Global Root CA」這個 2012 年的舊
# 根憑證，這個根憑證缺少 X509v3 Subject Key Identifier 欄位。較新版本的
# OpenSSL（3.6 以上，許多雲端主機的 Linux 基礎映像都已升級到這個版本）
# 會嚴格檢查這個欄位，因而直接拒絕連線，導致 httpx 丟出 SSL 憑證驗證失敗。
#
# 解法：改用作業系統原生的信任憑證庫（truststore 套件），取代 Python
# 預設的 certifi 憑證包，驗證行為較寬鬆，可以正常連上這類舊式政府網站。
# ──────────────────────────────────────────────
import truststore
truststore.inject_into_ssl()

import httpx
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("law-mcp")

# ──────────────────────────────────────────────
# 1. 常用法規代碼對照表（法規名稱 → pcode）
#    來源：法務部全國法規資料庫，每部法規在網址列都有固定的 pcode
#    例如民法：https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=B0000001
# ──────────────────────────────────────────────
PCODE_MAP: dict[str, str] = {
    "民法": "B0000001",
    "民事訴訟法": "B0010001",
    "刑法": "C0000001",
    "刑事訴訟法": "C0010001",
    "勞動基準法": "N0030001",
    "消費者保護法": "J0170001",
    "公平交易法": "J0150002",
    "個人資料保護法": "I0050021",
    "公司法": "J0080001",
    "強制執行法": "B0010004",
    "行政訴訟法": "A0030154",
    "訴願法": "A0030020",
    "國家賠償法": "I0020004",
    "著作權法": "J0070017",
    "專利法": "J0070007",
    "商標法": "J0070001",
    "營業秘密法": "J0080028",
    "保險法": "G0390002",
    "證券交易法": "G0400001",
    "銀行法": "G0380001",
    "勞工退休金條例": "N0030020",
    "性別平等工作法": "N0030014",
    "智慧財產案件審理法": "A0030215",
    "商業事件審理法": "B0010071",
    "土地法": "D0060001",
    "行政程序法": "A0030055",
    "行政罰法": "A0030210",
    "政府採購法": "A0030057",
    "中華民國憲法": "A0000001",
    "憲法訴訟法": "A0030159",
    "家事事件法": "B0010048",
    "仲裁法": "I0020001",
    "勞動事件法": "B0010064",
    "洗錢防制法": "G0380131",
    "稅捐稽徵法": "G0340001",
    "所得稅法": "G0340003",
    "票據法": "G0380028",
    "海商法": "K0070002",
    "破產法": "B0010006",
    "信託法": "I0020024",
    "建築法": "D0070109",
    "公寓大廈管理條例": "D0070118",
    "不動產經紀業管理條例": "D0060066",
    "道路交通管理處罰條例": "K0040012",
    "少年事件處理法": "C0010011",
    "社會秩序維護法": "D0080067",
    "遺產及贈與稅法": "G0340072",
    # 公開發行公司相關法規（金管會主管）
    "公開發行公司建立內部控制制度處理準則": "G0400045",
    "公開發行公司取得或處分資產處理準則": "G0400069",
    "公開發行公司年報應行記載事項準則": "G0400022",
    "公開發行公司資金貸與及背書保證處理準則": "G0400058",
    "公開發行公司董事會議事辦法": "G0400127",
    "公開發行公司審計委員會行使職權辦法": "G0400126",
    "公開發行公司出席股東會使用委託書規則": "G0400056",
    "公開發行公司獨立董事設置及應遵循事項辦法": "G0400125",
    "公開發行公司股東會議事手冊應行記載及遵行事項辦法": "G0400123",
    "公開收購公開發行公司有價證券管理辦法": "G0400063",
    "公開發行公司董事監察人股權成數及查核實施規則": "G0400051",
}

# 常見縮寫 → 正式名稱
LAW_ALIASES: dict[str, str] = {
    "消保法": "消費者保護法",
    "勞基法": "勞動基準法",
    "個資法": "個人資料保護法",
    "國賠法": "國家賠償法",
    "道交條例": "道路交通管理處罰條例",
    "證交法": "證券交易法",
    "公交法": "公平交易法",
    "強執法": "強制執行法",
    "家事法": "家事事件法",
    "少事法": "少年事件處理法",
    "社維法": "社會秩序維護法",
    "行程法": "行政程序法",
    "民訴法": "民事訴訟法",
    "刑訴法": "刑事訴訟法",
    "行訴法": "行政訴訟法",
    "智財法": "智慧財產案件審理法",
    "稅徵法": "稅捐稽徵法",
    "政採法": "政府採購法",
    "遺贈稅法": "遺產及贈與稅法",
    "公寓條例": "公寓大廈管理條例",
    "大廈條例": "公寓大廈管理條例",
    # 公開發行公司相關法規常用簡稱
    "內控準則": "公開發行公司建立內部控制制度處理準則",
    "取處準則": "公開發行公司取得或處分資產處理準則",
    "資產取得處分準則": "公開發行公司取得或處分資產處理準則",
    "年報準則": "公開發行公司年報應行記載事項準則",
    "資金貸與背書保證處理準則": "公開發行公司資金貸與及背書保證處理準則",
    "背書保證處理準則": "公開發行公司資金貸與及背書保證處理準則",
    "董事會議事辦法": "公開發行公司董事會議事辦法",
    "審計委員會職權辦法": "公開發行公司審計委員會行使職權辦法",
    "委託書規則": "公開發行公司出席股東會使用委託書規則",
    "獨立董事辦法": "公開發行公司獨立董事設置及應遵循事項辦法",
    "股東會議事手冊辦法": "公開發行公司股東會議事手冊應行記載及遵行事項辦法",
    "公開收購辦法": "公開收購公開發行公司有價證券管理辦法",
    "董監持股成數規則": "公開發行公司董事監察人股權成數及查核實施規則",
}

REGULATION_SINGLE_URL = "https://law.moj.gov.tw/LawClass/LawSingle.aspx"
REGULATION_ALL_URL = "https://law.moj.gov.tw/LawClass/LawAll.aspx"

_GARBAGE_INDICATORS = [
    "本網站係提供法規之最新動態資訊",
    "若有任何法律上的疑義",
    "著作權聲明",
    "隱私權保護",
    "網站安全政策",
    "瀏覽人次總計",
]


def _looks_like_article(text: str) -> bool:
    return not any(g in text for g in _GARBAGE_INDICATORS)


def resolve_pcode(name: str) -> Optional[str]:
    """把使用者輸入的法規名稱（含縮寫、模糊）轉成 pcode"""
    if name in PCODE_MAP:
        return PCODE_MAP[name]
    if name in LAW_ALIASES:
        return PCODE_MAP.get(LAW_ALIASES[name])
    # 模糊比對：輸入包含在正式名稱裡，或正式名稱包含輸入
    candidates = [
        (key, code) for key, code in PCODE_MAP.items()
        if name in key or key in name
    ]
    if candidates:
        candidates.sort(key=lambda x: len(x[0]))
        return candidates[0][1]
    return None


async def _fetch(url: str) -> str:
    """抓取網頁內容，內建 SSL 備援：

    優先使用（已透過 truststore 修正過的）標準驗證方式連線。如果部署環境
    仍然因為 law.moj.gov.tw 憑證鏈結的已知瑕疵而驗證失敗，才退回使用較
    寬鬆的驗證模式重試一次。這裡刻意把備援範圍鎖定在單一已知的政府網域，
    不是全域關閉 SSL 驗證，將風險降到最低。
    """
    headers = {"User-Agent": "Mozilla/5.0 (LawLookupMCP demo)"}
    try:
        async with httpx.AsyncClient(
            timeout=20.0, headers=headers, follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except httpx.ConnectError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e) and "SSL" not in str(e):
            raise
        logger.warning("標準 SSL 驗證失敗，改用寬鬆模式重試: %s", url)
        async with httpx.AsyncClient(
            timeout=20.0, headers=headers, follow_redirects=True, verify=False,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text


def _parse_single_article(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    result = {"law_name": "", "article_content": ""}

    title_el = soup.select_one("h2") or soup.select_one("title")
    if title_el:
        name = title_el.get_text(strip=True).split("-")[0].strip()
        if name and name not in ("條文內容", "法規內容", "全國法規資料庫"):
            result["law_name"] = name

    content_el = (
        soup.select_one(".law-article")
        or soup.select_one("#pnlContent")
        or soup.select_one(".content-law")
        or soup.select_one("pre")
    )
    if content_el:
        text = content_el.get_text(strip=True)
        if _looks_like_article(text):
            result["article_content"] = text

    return result


def _parse_law_all(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    result = {"law_name": "", "articles": []}

    title_el = soup.select_one("h2") or soup.select_one("title")
    if title_el:
        name = title_el.get_text(strip=True).split("-")[0].strip()
        if name and name not in ("條文內容", "法規內容", "全國法規資料庫"):
            result["law_name"] = name

    content_root = soup.select_one(".law-reg-content")
    rows = content_root.select(".row") if content_root else soup.select(".row")

    for row in rows:
        no_el = row.select_one(".col-no")
        data_el = row.select_one(".col-data")
        if not no_el or not data_el:
            continue
        number = no_el.get_text(strip=True)
        content = data_el.get_text("\n", strip=True)
        if number and content and _looks_like_article(content):
            result["articles"].append({"number": number, "content": content})

    return result


# ──────────────────────────────────────────────
# 2. 建立 MCP Server 並註冊工具
# ──────────────────────────────────────────────
mcp = MCPServer(
    name="taiwan-law-lookup",
    instructions=(
        "查詢台灣現行法規條文的工具。資料來源為法務部全國法規資料庫"
        "（law.moj.gov.tw）。每次回覆法律問題前，請優先呼叫這裡的工具"
        "取得最新條文內容，並附上 source_url 讓使用者可以核對原文，"
        "不要只憑記憶回答條號或條文內容。"
    ),
)


@mcp.tool()
def list_supported_laws() -> dict:
    """列出這個簡化版工具目前認得的法規名稱清單（約 45 部常用法規）。
    如果使用者要查的法規不在這份清單裡，請誠實告知目前查不到，
    不要用訓練記憶捏造條文。
    """
    return {"laws": sorted(PCODE_MAP.keys()), "count": len(PCODE_MAP)}


@mcp.tool()
async def get_law_article(law_name: str, article_no: str) -> dict:
    """查詢指定法規的單一條文最新內容。

    Args:
        law_name: 法規名稱，例如「勞動基準法」「民法」，也支援常見縮寫如「勞基法」。
        article_no: 條號，例如 "9" 或 "247-1"（之1 用連字號表示）。
    """
    pcode = resolve_pcode(law_name)
    if not pcode:
        return {
            "success": False,
            "error": f"目前查不到「{law_name}」，可能不在這個簡化版的內建清單中。",
        }

    url = f"{REGULATION_SINGLE_URL}?pcode={pcode}&flno={article_no}"
    try:
        html = await _fetch(url)
    except httpx.HTTPError as e:
        return {"success": False, "error": f"連線全國法規資料庫失敗：{e}"}

    parsed = _parse_single_article(html)
    if not parsed["article_content"]:
        history_url = f"https://law.moj.gov.tw/LawClass/LawHistory.aspx?pcode={pcode}"
        return {
            "success": True,
            "law_name": parsed["law_name"] or law_name,
            "article_no": article_no,
            "content": None,
            "note": (
                f"{law_name} 第 {article_no} 條目前查無條文內容，可能是「已刪除」"
                "或「條號從未存在」。請查閱下方的法規沿革連結，裡面會記載每次"
                "修正／刪除的日期，藉此判斷正確狀態，不要用訓練記憶推測。"
            ),
            "source_url": url,
            "law_history_url": history_url,
        }

    return {
        "success": True,
        "law_name": parsed["law_name"] or law_name,
        "article_no": article_no,
        "content": parsed["article_content"],
        "source_url": url,
    }


@mcp.tool()
async def get_law_full_text(law_name: str) -> dict:
    """查詢指定法規的完整條文（所有條號）。條文較多的法規回傳內容會比較長。

    Args:
        law_name: 法規名稱，例如「個人資料保護法」，也支援常見縮寫。
    """
    pcode = resolve_pcode(law_name)
    if not pcode:
        return {
            "success": False,
            "error": f"目前查不到「{law_name}」，可能不在這個簡化版的內建清單中。",
        }

    url = f"{REGULATION_ALL_URL}?pcode={pcode}"
    try:
        html = await _fetch(url)
    except httpx.HTTPError as e:
        return {"success": False, "error": f"連線全國法規資料庫失敗：{e}"}

    parsed = _parse_law_all(html)
    if not parsed["articles"]:
        return {
            "success": False,
            "error": f"沒有解析到「{law_name}」的條文內容，網站結構可能有變動。",
            "source_url": url,
        }

    return {
        "success": True,
        "law_name": parsed["law_name"] or law_name,
        "article_count": len(parsed["articles"]),
        "articles": parsed["articles"],
        "source_url": url,
    }


if __name__ == "__main__":
    import os

    # streamable-http 是 claude.ai 網頁版 Custom Connector 需要的傳輸方式。
    # 雲端主機（如 Render）會用環境變數 PORT 告訴你要監聽哪個埠號，
    # host 一定要設成 0.0.0.0，否則外部連不進來。
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, stateless_http=True)
