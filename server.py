"""
台灣法規查詢 MCP Server（簡化教學版）
資料來源：
  1. 法務部全國法規資料庫 (law.moj.gov.tw)                  → 對照表 PCODE_MAP
  2. 臺灣證券交易所法規分享知識庫 (twse-regulation.twse.com.tw) → 對照表 TWSE_FLCODE_MAP
     （證交所、櫃買中心共同訂定的守則，以及證交所自己的規章，全國法規資料庫查不到）
  3. 證券暨期貨法令判解查詢系統 (www.selaw.com.tw)           → 對照表 SELAW_SYSNO_MAP
     （櫃買中心的規章；每三天更新一次）

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
from urllib.parse import unquote

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
    "證券交易法施行細則": "G0400002",
    "商業會計法": "J0080009",
    "企業併購法": "J0080041",
    "證券投資人及期貨交易人保護法": "G0400038",
    "發行人募集與發行有價證券處理準則": "G0400023",
    "證券發行人財務報告編製準則": "G0400050",
    "公司募集發行有價證券公開說明書應行記載事項準則": "G0400019",
    "公開發行股票公司股務處理準則": "G0400006",
    "上市上櫃公司買回本公司股份辦法": "G0400061",
    "股票上市或於證券商營業處所買賣公司薪資報酬委員會設置及行使職權辦法": "G0400149",
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
    "中華民國刑法": "刑法",
    # 證交所法規分享知識庫（TWSE_FLCODE_MAP）常用簡稱
    "公司治理實務守則": "上市上櫃公司治理實務守則",
    "治理實務守則": "上市上櫃公司治理實務守則",
    "誠信經營守則": "上市上櫃公司誠信經營守則",
    "永續發展實務守則": "上市上櫃公司永續發展實務守則",
    "永續報告書作業辦法": "上市公司編製與申報永續報告書作業辦法",
    "上市審查準則": "臺灣證券交易所股份有限公司有價證券上市審查準則",
    "有價證券上市審查準則": "臺灣證券交易所股份有限公司有價證券上市審查準則",
    "上市公司重大訊息處理程序": "臺灣證券交易所股份有限公司對有價證券上市公司重大訊息之查證暨公開處理程序",
    "重大訊息處理程序": "臺灣證券交易所股份有限公司對有價證券上市公司重大訊息之查證暨公開處理程序",
    "營業細則": "臺灣證券交易所股份有限公司營業細則",
    "證交所營業細則": "臺灣證券交易所股份有限公司營業細則",
    "上市公司資訊申報作業辦法": "臺灣證券交易所股份有限公司對有價證券上市公司及境外指數股票型基金上市之境外基金機構資訊申報作業辦法",
    "私募應注意事項": "公開發行公司辦理私募有價證券應注意事項",
    "私募有價證券應注意事項": "公開發行公司辦理私募有價證券應注意事項",
    # 證券暨期貨法令判解查詢系統（SELAW_SYSNO_MAP，櫃買中心規章）常用簡稱
    "上櫃審查準則": "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣有價證券審查準則",
    "有價證券上櫃審查準則": "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣有價證券審查準則",
    "興櫃審查準則": "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣興櫃股票審查準則",
    "興櫃股票審查準則": "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣興櫃股票審查準則",
    "上櫃公司資訊申報作業辦法": "財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司資訊申報作業辦法",
    "上櫃公司重大訊息處理程序": "財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司重大訊息之查證暨公開處理程序",
    "櫃買中心業務規則": "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣有價證券業務規則",
    "業務規則": "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣有價證券業務規則",
}

REGULATION_SINGLE_URL = "https://law.moj.gov.tw/LawClass/LawSingle.aspx"
REGULATION_ALL_URL = "https://law.moj.gov.tw/LawClass/LawAll.aspx"

# ──────────────────────────────────────────────
# 1-2. 證交所法規分享知識庫對照表（法規名稱 → FLCODE）
#    用法跟上面的 pcode 一樣：在 twse-regulation.twse.com.tw 打開某部規章，
#    網址列 FLCODE= 後面那串就是代碼，例如營業細則：
#    https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx?FLCODE=FL007304
#    要新增規章，照樣加一行「"正式名稱": "FLxxxxxx",」即可。
# ──────────────────────────────────────────────
TWSE_FLCODE_MAP: dict[str, str] = {
    "上市上櫃公司治理實務守則": "FL020553",
    "上市上櫃公司誠信經營守則": "FL055768",
    "上市上櫃公司永續發展實務守則": "FL052368",
    "上市公司編製與申報永續報告書作業辦法": "FL075209",
    "臺灣證券交易所股份有限公司有價證券上市審查準則": "FL007326",
    "臺灣證券交易所股份有限公司對有價證券上市公司重大訊息之查證暨公開處理程序": "FL007111",
    "臺灣證券交易所股份有限公司營業細則": "FL007304",
    "臺灣證券交易所股份有限公司對有價證券上市公司及境外指數股票型基金上市之境外基金機構資訊申報作業辦法": "FL007250",
    # 以下是知識庫「相關法規」分頁收錄的金管會行政規則
    "公開發行公司辦理私募有價證券應注意事項": "FL037281",
}

TWSE_ALL_URL = "https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx"
TWSE_SINGLE_URL = "https://twse-regulation.twse.com.tw/TW/law/DOC01.aspx"
TWSE_HISTORY_URL = "https://twse-regulation.twse.com.tw/TW/law/DAT01.aspx"

# ──────────────────────────────────────────────
# 1-3. 證券暨期貨法令判解查詢系統對照表（法規名稱 → sysNumber）
#    櫃買中心沒有自己的法規資料庫，規章要從這個系統（證基會維運）查。
#    在 www.selaw.com.tw 打開某部規章，網址列 sysNumber= 後面那串就是代碼。
#    注意：這個系統的資料每三天更新一次，剛修正的條文可能晚幾天才出現。
# ──────────────────────────────────────────────
SELAW_SYSNO_MAP: dict[str, str] = {
    "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣有價證券審查準則": "LW10812073",
    "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣興櫃股票審查準則": "LW10825672",
    "財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司資訊申報作業辦法": "LW10819011",
    "財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司重大訊息之查證暨公開處理程序": "LW10812093",
    "財團法人中華民國證券櫃檯買賣中心證券商營業處所買賣有價證券業務規則": "LW10812069",
}

SELAW_BASE_URL = "https://www.selaw.com.tw/Chinese/RegulatoryInformationResult"
SELAW_ARTICLE_URL = "https://www.selaw.com.tw/Chinese/RegulatoryInformationResult/Article"

# 各來源的對照表與顯示名稱
SOURCES: dict[str, tuple[dict[str, str], str]] = {
    "moj": (PCODE_MAP, "法務部全國法規資料庫"),
    "twse": (TWSE_FLCODE_MAP, "臺灣證券交易所法規分享知識庫"),
    "selaw": (SELAW_SYSNO_MAP, "證券暨期貨法令判解查詢系統（每三天更新）"),
}

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


def resolve_law(name: str) -> Optional[tuple[str, str, str, bool]]:
    """把使用者輸入的法規名稱（含縮寫、模糊）轉成 (來源, 代碼, 正式名稱, 是否精確)。

    來源為 SOURCES 的鍵（"moj" 或 "twse"）。「是否精確」為 False 代表是靠
    模糊比對找到的，呼叫端會在結果裡註明實際查的是哪一部，避免張冠李戴。
    """
    name = name.strip().replace("「", "").replace("」", "")
    name = name.replace("台灣證券交易所", "臺灣證券交易所")
    name = LAW_ALIASES.get(name, name)
    for source, (table, _) in SOURCES.items():
        if name in table:
            return source, table[name], name, True
    if len(name) < 2:
        return None
    # 模糊比對：只接受「輸入是正式名稱的一部分」。
    # 反方向（正式名稱是輸入的一部分）刻意不比對：那會把「證券交易法施行細則」
    # 當成「證券交易法」，回傳另一部法規的條文。
    candidates = [
        (key, source, code)
        for source, (table, _) in SOURCES.items()
        for key, code in table.items()
        if name in key
    ]
    if candidates:
        candidates.sort(key=lambda x: len(x[0]))
        key, source, code = candidates[0]
        return source, code, key, False
    return None


def _match_info(requested: str, name: str, exact: bool, source: str) -> dict:
    """每個查詢結果都附上的欄位：資料來自哪個網站，以及模糊比對時的提醒。"""
    info = {"source_site": SOURCES[source][1]}
    if not exact:
        info["requested_name"] = requested
        info["match_note"] = (
            f"清單中沒有名稱完全等於「{requested}」的法規，以下是名稱最接近的"
            f"「{name}」的內容。請確認這是否為使用者要查的法規；若不是，"
            "請告知查無此法規，不要把這份內容當成使用者要的那一部。"
        )
    return info


# 允許在 SSL 驗證失敗時退回寬鬆模式的網域（僅限已知的官方法規網站）
_SSL_FALLBACK_HOSTS = {
    "law.moj.gov.tw", "twse-regulation.twse.com.tw", "www.selaw.com.tw",
}


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
        if httpx.URL(url).host not in _SSL_FALLBACK_HOSTS:
            raise
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


def _norm_article_no(no: str) -> str:
    """把「第 2 條之 1」「2之1」「2-1」等寫法統一成 "2-1" 以便比對。"""
    no = re.sub(r"[第條點\s]", "", no)
    return no.replace("之", "-").replace("－", "-")


_TWSE_ARTICLE_LABEL = re.compile(r"^(第\s*\d+(\s*-\s*\d+)*\s*[條點]|\d+(\s*-\s*\d+)*)$")


def _parse_twse_all(html: str) -> dict:
    """解析證交所法規分享知識庫的「所有條文」頁面。

    頁面是一張表格，每一列的第一格是連到單一條文頁的連結（文字「第 N 條」，
    網址帶 FLNO=N），第二格是條文內容。這裡以那個連結為定位點，不依賴
    CSS class，網站改版時比較不容易壞。
    """
    soup = BeautifulSoup(html, "lxml")
    result = {
        "amended_date": "",
        "not_yet_effective": False,
        "unstructured": False,
        "articles": [],
    }

    page_text = soup.get_text(" ", strip=True)
    m = re.search(r"修正日期\s*[：:]?\s*(民國\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日)", page_text)
    if m:
        result["amended_date"] = re.sub(r"\s+", " ", m.group(1))
    result["not_yet_effective"] = "尚未生效" in page_text

    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"DOC01\.aspx\?.*?FLNO=([^&#]+)", a["href"], re.I)
        if not m:
            continue
        label = a.get_text(" ", strip=True)
        # 連結文字是「第 N 條」，以點次編排的規章（如私募應注意事項）則只有數字。
        # 同一列另有一個「相關資訊」連結，網址相同，要跳過。
        if not _TWSE_ARTICLE_LABEL.match(label):
            continue
        number = unquote(m.group(1)).strip()
        row = a.find_parent("tr")
        if number in seen or row is None:
            continue

        pres = row.find_all("pre")
        if pres:
            content = "\n".join(p.get_text() for p in pres)
        else:
            own_cell = a.find_parent(["td", "th"])
            texts = [
                c.get_text("\n", strip=True)
                for c in row.find_all(["td", "th"]) if c is not own_cell
            ]
            texts = [t for t in texts if t and t != "相關資訊"]
            content = max(texts, key=len) if texts else ""
        content = "\n".join(
            line.rstrip() for line in content.strip("\n").splitlines()
        ).strip()
        if content:
            seen.add(number)
            result["articles"].append({"number": number, "content": content})

    # 有些規章不是「第 N 條」的格式（例如以一、二、三點編排），沒有逐條連結，
    # 這時把頁面上的條文區塊整段回傳。
    if not result["articles"]:
        blocks = [p.get_text().strip("\n") for p in soup.find_all("pre")]
        blocks = [b for b in blocks if b.strip()]
        if blocks:
            result["unstructured"] = True
            result["articles"].append({"number": "全文", "content": "\n".join(blocks)})

    return result


def _twse_meta(parsed: dict) -> dict:
    meta = {}
    if parsed["amended_date"]:
        meta["amended_date"] = parsed["amended_date"]
    if parsed["not_yet_effective"]:
        meta["effective_note"] = (
            "網站標示本法規部分或全部條文尚未生效，請開啟 source_url 確認"
            "各條文的生效日期後再引用。"
        )
    return meta


# ──────────────────────────────────────────────
# 證券暨期貨法令判解查詢系統（selaw）
#
# 這個網站的「所有條文」不是固定網址：要先打開法規頁，再把頁面上的隱藏
# 表單（sysNumber、releaseDate、驗證碼）送到 /Article，而且伺服器會用
# cookie 記住「目前在看哪一部」。所以每次查詢都開一個全新的連線階段，
# 照瀏覽器的順序走兩步，最後核對回傳頁面的 sysNumber 確實是要查的那一部，
# 不符就寧可回報失敗，也不回傳別部法規的條文。
# ──────────────────────────────────────────────
class _SelawMismatch(Exception):
    pass


def _selaw_page_info(html: str) -> dict:
    """讀出頁面最上方的法規名稱、sysNumber、發佈日期、沿革資訊與隱藏表單欄位。"""
    soup = BeautifulSoup(html, "lxml")
    info = {"sysno": "", "name": "", "date": "", "amendment": "", "form": {}}

    top = soup.select_one("table.con-table-top") or soup
    for a in top.find_all("a", href=True):
        m = re.search(r"RegulatoryInformationResult\?sysNumber=(\w+)", a["href"])
        if m:
            info["sysno"] = m.group(1)
            info["name"] = a.get_text(strip=True)
            break
    for td in top.find_all("td"):
        label = td.get_text(strip=True)
        value_td = td.find_next_sibling("td")
        if value_td is None:
            continue
        if label == "發佈日期":
            info["date"] = value_td.get_text(strip=True)
        elif label == "沿革資訊":
            info["amendment"] = re.sub(r"\s+", "", value_td.get_text())

    form = soup.find("form", id="formInfo")
    if form:
        for inp in form.find_all("input", attrs={"type": "hidden"}):
            if inp.get("name"):
                info["form"][inp["name"]] = inp.get("value", "")
    return info


async def _selaw_fetch_all_page(sysno: str, verify: bool = True) -> str:
    """用全新的連線階段取得某部法規的「所有條文」頁面 HTML。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9",
    }
    base_url = f"{SELAW_BASE_URL}?sysNumber={sysno}"
    async with httpx.AsyncClient(
        timeout=30.0, headers=headers, follow_redirects=True, verify=verify,
    ) as client:
        first = await client.get(base_url)
        first.raise_for_status()
        page = _selaw_page_info(first.text)
        if page["sysno"] != sysno:
            raise _SelawMismatch(f"法規頁回傳的是 {page['sysno'] or '不明'}")

        params = dict(page["form"])
        params["sysNumber"] = sysno
        second = await client.get(
            SELAW_ARTICLE_URL, params=params, headers={"Referer": base_url},
        )
        second.raise_for_status()
        if _selaw_page_info(second.text)["sysno"] != sysno:
            raise _SelawMismatch("所有條文頁回傳的不是這部法規")
        return second.text


def _parse_selaw_all(html: str) -> dict:
    """解析 selaw 的「所有條文」頁。

    條文區塊（div.con-rules）底下是一串平行的元素：ol.rules-lv01 放條號，
    緊接著的 ol.rules-lv02 放該條內容（項、款、目以巢狀 ol 表示），有附表的
    條文後面還會跟一個放附件連結的 div。
    """
    soup = BeautifulSoup(html, "lxml")
    page = _selaw_page_info(html)
    result = {
        "law_name": page["name"],
        "amended_date": page["date"],
        "latest_amendment": page["amendment"],
        "unstructured": False,
        "articles": [],
    }
    con = soup.select_one("div.con-rules")
    if con is None:
        return result

    def lines_of(el) -> list[str]:
        out = []
        for s in el.find_all(string=True):
            text = s.strip()
            if not text:
                continue
            depth = sum(1 for p in s.parents if p.name == "ol" and p is not el)
            out.append("  " * depth + text)
        return out

    current = None
    loose: list[str] = []
    for child in con.find_all(True, recursive=False):
        classes = child.get("class") or []
        if "title-rule-book" in classes:
            continue  # 章節標題
        if child.name == "ol" and "rules-lv01" in classes:
            label = child.get_text(strip=True)
            current = {"number": _norm_article_no(label), "lines": []}
            result["articles"].append(current)
        elif current is not None:
            current["lines"].extend(lines_of(child))
        else:
            loose.extend(lines_of(child))

    result["articles"] = [
        {"number": a["number"], "content": "\n".join(a["lines"])}
        for a in result["articles"] if a["number"]
    ]
    # 沒有條號的規章（以點次或段落編排）整段回傳
    if not result["articles"] and loose:
        result["unstructured"] = True
        result["articles"].append({"number": "全文", "content": "\n".join(loose)})
    return result


def _selaw_meta(parsed: dict) -> dict:
    meta = {}
    if parsed["amended_date"]:
        meta["amended_date"] = parsed["amended_date"]
    if parsed["latest_amendment"]:
        meta["latest_amendment"] = parsed["latest_amendment"]
    return meta


async def _load_selaw(name: str, sysno: str) -> dict:
    """取得並解析 selaw 的一部法規；失敗時回傳帶 error 的 dict。"""
    url = f"{SELAW_BASE_URL}?sysNumber={sysno}"
    site = "證券暨期貨法令判解查詢系統"
    html = None
    problem = ""
    verify = True
    for _ in range(3):
        try:
            html = await _selaw_fetch_all_page(sysno, verify=verify)
            break
        except _SelawMismatch as e:
            problem = f"{site}回傳的內容與要查的法規不符（{e}），為避免引用錯誤的條文，不予回傳。"
        except httpx.ConnectError as e:
            if verify and ("CERTIFICATE_VERIFY_FAILED" in str(e) or "SSL" in str(e)):
                logger.warning("selaw 標準 SSL 驗證失敗，改用寬鬆模式重試")
                verify = False
                continue
            return {"error": f"連線{site}失敗：{e}", "source_url": url}
        except httpx.HTTPError as e:
            return {"error": f"連線{site}失敗：{e}", "source_url": url}
    if html is None:
        return {"error": problem, "source_url": url}

    parsed = _parse_selaw_all(html)
    if not parsed["articles"]:
        return {
            "error": f"沒有解析到「{name}」的條文內容，網站結構可能有變動。",
            "source_url": url,
        }
    parsed["source_url"] = url
    return parsed


# ──────────────────────────────────────────────
# 2. 建立 MCP Server 並註冊工具
# ──────────────────────────────────────────────
mcp = MCPServer(
    name="taiwan-law-lookup",
    instructions=(
        "查詢台灣現行法規條文的工具。資料來源為法務部全國法規資料庫"
        "（law.moj.gov.tw），以及臺灣證券交易所法規分享知識庫"
        "（twse-regulation.twse.com.tw，收錄上市上櫃公司治理、誠信經營、"
        "永續發展等守則與證交所規章）、證券暨期貨法令判解查詢系統"
        "（www.selaw.com.tw，收錄櫃買中心規章，每三天更新一次，剛修正的"
        "條文可能尚未反映）。每次回覆法律問題前，請優先呼叫這裡"
        "的工具取得最新條文內容，並附上 source_url 讓使用者可以核對原文，"
        "不要只憑記憶回答條號或條文內容。結果若帶有 match_note，代表查到的"
        "不是使用者輸入的那個名稱，務必先確認是否為同一部法規。"
    ),
)


@mcp.tool()
def list_supported_laws() -> dict:
    """列出這個簡化版工具目前認得的法規名稱清單，並依資料來源分組
    （法務部全國法規資料庫、臺灣證券交易所法規分享知識庫、證券暨期貨法令判解查詢系統）。
    如果使用者要查的法規不在這份清單裡，請誠實告知目前查不到，
    不要用訓練記憶捏造條文。
    """
    by_source = {label: sorted(table.keys()) for table, label in SOURCES.values()}
    laws = sorted(name for names in by_source.values() for name in names)
    return {"laws": laws, "count": len(laws), "by_source": by_source}


@mcp.tool()
async def get_law_article(law_name: str, article_no: str) -> dict:
    """查詢指定法規的單一條文最新內容。

    Args:
        law_name: 法規名稱，例如「勞動基準法」「民法」，也支援常見縮寫如「勞基法」。
        article_no: 條號，例如 "9" 或 "247-1"（之1 用連字號表示）。
    """
    resolved = resolve_law(law_name)
    if not resolved:
        return {
            "success": False,
            "error": f"目前查不到「{law_name}」，可能不在這個簡化版的內建清單中。",
        }
    source, pcode, name, exact = resolved
    info = _match_info(law_name, name, exact, source)

    if source == "twse":
        return await _get_twse_article(name, pcode, article_no, info)
    if source == "selaw":
        return await _get_selaw_article(name, pcode, article_no, info)

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
            "law_name": name,
            "article_no": article_no,
            "content": None,
            "note": (
                f"{name} 第 {article_no} 條目前查無條文內容，可能是「已刪除」"
                "或「條號從未存在」。請查閱下方的法規沿革連結，裡面會記載每次"
                "修正／刪除的日期，藉此判斷正確狀態，不要用訓練記憶推測。"
            ),
            "source_url": url,
            "law_history_url": history_url,
            **info,
        }

    return {
        "success": True,
        "law_name": name,
        "article_no": article_no,
        "content": parsed["article_content"],
        "source_url": url,
        **info,
    }


async def _get_selaw_article(name: str, sysno: str, article_no: str, info: dict) -> dict:
    """selaw 來源的單一條文：抓「所有條文」頁再挑出指定條號。"""
    parsed = await _load_selaw(name, sysno)
    if "error" in parsed:
        return {"success": False, **parsed, **info}

    base = {
        "success": True,
        "law_name": parsed["law_name"] or name,
        "article_no": article_no,
    }
    tail = {"source_url": parsed["source_url"], **_selaw_meta(parsed), **info}
    if parsed["unstructured"]:
        return {
            **base,
            "content": parsed["articles"][0]["content"],
            "note": "這部規章不是以「第 N 條」編排，無法按條號擷取，以下為全文，請自行找出對應段落。",
            **tail,
        }

    wanted = _norm_article_no(article_no)
    for art in parsed["articles"]:
        if art["number"] == wanted:
            return {**base, "content": art["content"], **tail}
    return {
        **base,
        "content": None,
        "note": (
            f"{name} 查無第 {article_no} 條，可能是條號不存在。請開啟 source_url "
            "點選「法規沿革」確認，不要用訓練記憶推測。"
        ),
        **tail,
    }


async def _get_twse_article(name: str, flcode: str, article_no: str, info: dict) -> dict:
    """證交所來源的單一條文：抓「所有條文」頁再挑出指定條號。

    這樣單條與全文共用同一套解析，也能順便取得修正日期。
    """
    all_url = f"{TWSE_ALL_URL}?FLCODE={flcode}"
    try:
        html = await _fetch(all_url)
    except httpx.HTTPError as e:
        return {"success": False, "error": f"連線證交所法規分享知識庫失敗：{e}"}

    parsed = _parse_twse_all(html)
    if not parsed["articles"]:
        return {
            "success": False,
            "error": f"沒有解析到「{name}」的條文內容，網站結構可能有變動。",
            "source_url": all_url,
            **info,
        }

    base = {"success": True, "law_name": name, "article_no": article_no}
    if parsed["unstructured"]:
        return {
            **base,
            "content": parsed["articles"][0]["content"],
            "note": "這部規章不是以「第 N 條」編排，無法按條號擷取，以下為全文，請自行找出對應段落。",
            "source_url": all_url,
            **_twse_meta(parsed),
            **info,
        }

    wanted = _norm_article_no(article_no)
    for art in parsed["articles"]:
        if _norm_article_no(art["number"]) == wanted:
            return {
                **base,
                "content": art["content"],
                "source_url": f"{TWSE_SINGLE_URL}?FLCODE={flcode}&FLNO={art['number']}",
                **_twse_meta(parsed),
                **info,
            }

    return {
        **base,
        "content": None,
        "note": (
            f"{name} 查無第 {article_no} 條，可能是條號不存在。請查閱下方的"
            "歷史沿革連結確認，不要用訓練記憶推測。"
        ),
        "source_url": all_url,
        "law_history_url": f"{TWSE_HISTORY_URL}?FLCODE={flcode}",
        **_twse_meta(parsed),
        **info,
    }


@mcp.tool()
async def get_law_full_text(law_name: str) -> dict:
    """查詢指定法規的完整條文（所有條號）。條文較多的法規回傳內容會比較長。

    Args:
        law_name: 法規名稱，例如「個人資料保護法」，也支援常見縮寫。
    """
    resolved = resolve_law(law_name)
    if not resolved:
        return {
            "success": False,
            "error": f"目前查不到「{law_name}」，可能不在這個簡化版的內建清單中。",
        }
    source, pcode, name, exact = resolved
    info = _match_info(law_name, name, exact, source)

    if source == "selaw":
        parsed = await _load_selaw(name, pcode)
        if "error" in parsed:
            return {"success": False, **parsed, **info}
        return {
            "success": True,
            "law_name": parsed["law_name"] or name,
            "article_count": len(parsed["articles"]),
            "articles": parsed["articles"],
            "source_url": parsed["source_url"],
            **_selaw_meta(parsed),
            **info,
        }

    if source == "twse":
        url = f"{TWSE_ALL_URL}?FLCODE={pcode}"
        site = "證交所法規分享知識庫"
    else:
        url = f"{REGULATION_ALL_URL}?pcode={pcode}"
        site = "全國法規資料庫"
    try:
        html = await _fetch(url)
    except httpx.HTTPError as e:
        return {"success": False, "error": f"連線{site}失敗：{e}"}

    parsed = _parse_twse_all(html) if source == "twse" else _parse_law_all(html)
    if not parsed["articles"]:
        return {
            "success": False,
            "error": f"沒有解析到「{name}」的條文內容，網站結構可能有變動。",
            "source_url": url,
            **info,
        }

    return {
        "success": True,
        "law_name": name,
        "article_count": len(parsed["articles"]),
        "articles": parsed["articles"],
        "source_url": url,
        **(_twse_meta(parsed) if source == "twse" else {}),
        **info,
    }


if __name__ == "__main__":
    import os

    # streamable-http 是 claude.ai 網頁版 Custom Connector 需要的傳輸方式。
    # 雲端主機（如 Render）會用環境變數 PORT 告訴你要監聽哪個埠號，
    # host 一定要設成 0.0.0.0，否則外部連不進來。
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, stateless_http=True)
