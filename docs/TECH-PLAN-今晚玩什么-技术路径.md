# 「今晚玩什么」(`coop-night-picker`) — 架构思路与详细技术路径

> **文档用途**：本文件可直接作为工程规范（Spec）喂给 GPT / DeepSeek / Claude Code 等 AI 编程助手，按 Phase 0 → Phase 4 逐步生成并运行代码。
> **关联文档**：已勘误的调研报告 [`REPORT-今晚玩什么-MCP调研报告.md`](./REPORT-今晚玩什么-MCP调研报告.md)（v1.1，原版备份于 `.v1.0.bak.md`）。

---

## 一、 核心思路总结（为什么这么设计）

### 1.1 痛点与产品定位
- **真实场景**：周末/下班后和朋友开黑，想找 Steam 联机游戏（尤其是免费 Demo 或便宜好玩的合作游戏），但试玩踩雷率极高，每次选游戏要花 30 分钟。
- **核心交互**：
  1. 用户说：「今晚想玩联机游戏」
  2. AI **只追问一轮**（最多 4 个核心问题：几个人？恐不恐怖？免费 Demo 还是付费？玩多久？）
  3. 返回**按 Steam 九档评价（好评如潮 → 差评如潮）分组的推荐榜单 + 星级**，每款游戏附**真实中文评测摘录的推荐理由**，并单独列出**避雷榜**（高频差评槽点：掉线/外挂/内容少/假联机）。

### 1.2 国内数据困局与破局方案（核心架构决策）
调研实测发现，国内网络直连 Steam 存在严重限制：
- **能拿到的**：通过 `steamstore-a.akamaihd.net`（Akamai CDN）或本地代理，可以拿到游戏详情（`appdetails`）、搜索结果卡片（含总好评率与档位 tooltip）、商店 HTML 页（含标签和分语言好评率摘要）。
- **拿不到的（致命卡点）**：Steam 评测正文接口 `/appreviews/<appid>?json=1` 在 CDN 主机上会被 **HTTP 302 强制重定向回已被阻断的 `store.steampowered.com`**，导致连接重置或 `IncompleteRead(0 bytes read)`。而在本地 Clash 等代理下，默认分流规则常把 `steampowered.com` 设为 `DIRECT`（为了照顾国内下载 CDN），导致评测接口依然走直连被墙。

**破局方案：「海外离线预抓取 / 本地代理抓取 + 本地 SQLite 快照 + 纯本地毫秒级 MCP」**

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ 数据生产层（每日/每周定时运行，或本地代理修正后运行）                   │
│  GitHub Actions (海外 Ubuntu 免费机房，无网络封锁)                      │
│    ├─ 1. 拉取候选池：在线合作(tags=3843/category3=38) + Demo(cat1=10)   │
│    ├─ 2. 抓取详情与商店页：价格、Demo关联、标签(1667恐怖等)、简中好评率  │
│    ├─ 3. 抓取中文评测原文：appreviews (最有帮助好评 + 差评)             │
│    └─ 4. 关键词打标与抽取：联机体验/网络掉线/恐怖度/雷点 → 存入 SQLite  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ 产出 data/games.sqlite (几 MB)
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ MCP 服务层（用户本地运行，零 API Key、零网络依赖、毫秒级响应）          │
│  FastMCP Server (`coop-night-picker`)                                   │
│    ├─ Tool 1: recommend_coop_games (缺参返回追问清单；齐参查表分组输出) │
│    ├─ Tool 2: get_game_card        (单游戏九档口碑 + 真实评测原文引用)  │
│    ├─ Tool 3: resolve_game         (模糊游戏名 → AppID 与基础信息)      │
│    └─ Tool 4: log_play             (记录"玩过/踩雷/想玩"，自动排除)     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 二、 调研报告（v1.0）核心勘误清单

在交给 AI 写代码前，**必须遵守以下修正后的技术事实**（已同步更新至调研报告 v1.1）：

1. **九档评价枚举（以正文 §3.4 为唯一标准，废弃附录错误映射）**：
   - `9`=好评如潮 (Overwhelmingly Positive, ★★★★★ 5.0)
   - `8`=特别好评 (Very Positive, ★★★★½ 4.5)
   - `7`=好评 (Positive, ★★★★ 4.0)
   - `6`=多半好评 (Mostly Positive, ★★★½ 3.5)
   - `5`=褒贬不一 (Mixed, ★★★ 3.0)
   - `4`=多半差评 (Mostly Negative, ★★½ 2.5)
   - `3`=差评 (Negative, ★★ 2.0)
   - `2`=特别差评 (Very Negative, ★½ 1.5)
   - `1`=差评如潮 (Overwhelmingly Negative, ★ 1.0)
   - `0`=评测数不足（单独归入「试玩友好 / 未知区」）
2. **Steam 语言参数**：
   - 简体中文参数在 Steam 全系接口中均为 **`l=schinese`**（而非 `l=zh`）。原报告用 `l=zh` 导致 categories 返回英文。即便如此，代码中仍需内置一份 `category_id → 中文名` 字典兜底。
3. **`appreviews` 接口正确 URL 格式**：
   - 正确路径是 `https://store.steampowered.com/appreviews/<appid>?json=1&language=schinese&filter=all&num_per_page=50&purchase_type=all`（`appid` 在 URL 路径中，不是 `?appids=`）。
   - 请求时**必须设置 `follow_redirects=False`**，一旦检测到 `status_code in (301, 302)`，说明落入了 CDN 的重定向陷阱，应立即切换代理出口或降级为商店页 HTML 解析。
4. **搜索接口 `search/results` 的分类参数**：
   - `category1` 表示软件大类：`category1=998` 为正式游戏，`category1=10` 为 Demo（试玩版）。
   - 联机特性在 `category3`：`category3=38`（在线合作）、`category3=9`（合作）、`category3=39`（同屏/分屏合作）。
   - 标签过滤用 `tags`：`tags=3843`（在线合作）、`tags=1685`（合作）、`tags=1667`（恐怖）、`tags=1721`（心理恐怖）、`tags=3978`（生存恐怖）。
5. **恐怖判定不能只看搜索列表的 `data-ds-tagids`**：
   - 搜索结果卡片里的 `data-ds-tagids` 往往只截取前几个热门标签。为防止漏判恐怖游戏，构建数据库时**必须额外跑一次 `tags=1667`（及 `1721,3978`）的专项搜索**，将命中的 `appid` 集合打上 `is_horror=1` 标记，同时结合商店详情页的 `InitAppTagModal` 标签列表综合判定。

---

## 三、 项目目录结构

请 AI 助手在工作区创建如下标准 Python 项目结构：

```text
coop-night-picker/
├── pyproject.toml                 # 依赖管理：mcp[cli], httpx, beautifulsoup4, lxml, pydantic
├── README.md                      # 使用说明、数据源声明、客户端配置指南
├── .env.example                   # 可选环境变量（STEAM_PROXY, STEAM_HOST 等）
├── .github/
│   └── workflows/
│       └── update_snapshot.yml    # GitHub Actions 定时海外抓取流水线
├── data/
│   ├── games.sqlite               # 核心离线快照数据库（随仓库/Release 分发）
│   └── user_log.json              # 用户本地"玩过/踩雷/想玩"记录（git 忽略）
├── scripts/
│   ├── probe_network.py           # Phase 0：本机直连/代理/CDN 连通性一键诊断脚本
│   ├── build_snapshot.py          # Phase 1：候选池拉取 + 详情/评测抓取 + 入库脚本
│   └── verify_snapshot.py         # 数据质量校验脚本（检查九档分布、引用子串真实性）
└── src/
    └── coop_night_picker/
        ├── __init__.py
        ├── constants.py           # 九档映射表、Category/Tag ID 字典、关键词正则表
        ├── db.py                  # SQLite 查询与 schema 初始化封装
        ├── steam_client.py        # 统一 HTTP 适配层（多 Host 回退、防 302、限流重试）
        ├── extractor.py           # 商店 HTML 解析与评测文本关键词抽句器
        └── server.py              # FastMCP Server 主入口（4 Tools + 1 Prompt）
```

---

## 四、 分阶段实施步骤（可直接复制给 AI 执行）

### Phase 0：本机网络与代理快速诊断（10 分钟）

**目标**：验证本机代理（如 `http://127.0.0.1:7890`）能否直接跑通 `appreviews`，为本地开发调试铺路。

**执行步骤**：
1. 编写 `scripts/probe_network.py`，测试以下 4 个组合对 `appdetails`、`search/results`、`appreviews/548430?json=1` 的响应状态码与耗时：
   - 组合 A：无代理 + `https://store.steampowered.com`
   - 组合 B：无代理 + `https://steamstore-a.akamaihd.net`
   - 组合 C：显式传入代理 `http://127.0.0.1:7890` + `https://store.steampowered.com`
   - 组合 D：显式传入代理 `http://127.0.0.1:7890` + `https://steamstore-a.akamaihd.net`
2. **关键代码要求**：
   - 使用 `httpx.Client(proxy=..., follow_redirects=False, timeout=15.0, headers={"User-Agent": "Mozilla/5.0 ..."})`。
   - 如果组合 C 的 `appreviews` 依然报 `ConnectError` / `ReadError`，提示用户在 Clash / 代理软件中添加两条最高优先级规则：
     - `DOMAIN-SUFFIX,steampowered.com,PROXY`
     - `DOMAIN-SUFFIX,steamcommunity.com,PROXY`
   - 若用户不想改代理规则，本地开发直接启用 **降级模式（Fallback Mode）**：用 `steamstore-a.akamaihd.net/app/<id>/?l=schinese&cc=cn` 解析九档与标签，把 `appreviews` 留给 GitHub Actions 海外跑。

---

### Phase 1：数据库 Schema 与数据抓取管道（`db.py` & `build_snapshot.py`）

#### Step 1.1：定义常量字典（`src/coop_night_picker/constants.py`）

```python
REVIEW_TIERS = {
    9: {"en": "Overwhelmingly Positive", "zh": "好评如潮", "stars": "★★★★★", "score": 5.0},
    8: {"en": "Very Positive",           "zh": "特别好评", "stars": "★★★★½", "score": 4.5},
    7: {"en": "Positive",                "zh": "好评",     "stars": "★★★★",  "score": 4.0},
    6: {"en": "Mostly Positive",         "zh": "多半好评", "stars": "★★★½", "score": 3.5},
    5: {"en": "Mixed",                   "zh": "褒贬不一", "stars": "★★★",  "score": 3.0},
    4: {"en": "Mostly Negative",         "zh": "多半差评", "stars": "★★½", "score": 2.5},
    3: {"en": "Negative",                "zh": "差评",     "stars": "★★",  "score": 2.0},
    2: {"en": "Very Negative",           "zh": "特别差评", "stars": "★½", "score": 1.5},
    1: {"en": "Overwhelmingly Negative", "zh": "差评如潮", "stars": "★",  "score": 1.0},
    0: {"en": "No Enough Reviews",       "zh": "评测不足/试玩区", "stars": "☆", "score": 0.0},
}

CATEGORY_ZH = {
    1: "多人", 2: "单人", 9: "合作", 10: "游戏试玩(Demo)", 20: "大型多人在线",
    24: "同屏/分屏", 27: "跨平台多人", 36: "在线PvP", 37: "同屏/分屏PvP",
    38: "在线合作", 39: "同屏/分屏合作", 44: "远程同乐", 47: "局域网PvP",
    48: "局域网合作", 49: "玩家对战(PvP)",
}

HORROR_TAG_IDS = {1667, 1721, 3978}          # 恐怖、心理恐怖、生存恐怖
COOP_TAG_IDS = {1685, 3843, 3841, 4508, 7368} # 合作、在线合作、本地合作、合作战役、本地多人

# 评测文本句子级切分与关键词维度表
REVIEW_KEYWORDS = {
    "coop_quality": r"联机|开黑|组队|队友|朋友|基友|双人|四人|多人|配合|欢乐|友尽",
    "network":      r"延迟|卡顿|掉线|加速器|网络|ping|断连|服务器|连不上|房主",
    "session":      r"一局|几小时|小时|通宵|一晚上|耐玩|流程|通关|快节奏",
    "horror":       r"吓人|恐怖|高能|jump\s*scare|跳脸|诡异|害怕|胆小|微恐",
    "dealbreaker":  r"退款|垃圾|坐牢|快逃|别买|外挂|挂壁|闪退|恶性bug|半成品|鬼服|没人|假联机",
}
```

#### Step 1.2：SQLite 表结构（`src/coop_night_picker/db.py`）

使用标准库 `sqlite3`，创建 2 张核心表：

```sql
CREATE TABLE IF NOT EXISTS games (
    appid INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT DEFAULT 'game',               -- 'game' 或 'demo'
    is_free INTEGER DEFAULT 0,              -- 1=免费, 0=付费
    has_demo INTEGER DEFAULT 0,             -- 1=有试玩Demo或本身是Demo
    demo_appid INTEGER,                     -- 关联的 Demo appid（若有）
    fullgame_appid INTEGER,                 -- 若自身是 Demo，指向正式版 appid
    price_cny REAL DEFAULT 0.0,             -- 人民币现价（元，如 32.4）
    discount_pct INTEGER DEFAULT 0,         -- 折扣百分比（如 70 表示 -70%）
    online_coop INTEGER DEFAULT 0,          -- 1=支持在线合作(cat 38 或 tag 3843)
    local_coop INTEGER DEFAULT 0,           -- 1=支持同屏/本地合作(cat 39/24 或 tag 3841)
    remote_play INTEGER DEFAULT 0,          -- 1=支持远程同乐(cat 44)
    max_players INTEGER,                    -- 最大联机人数（来自 PCGamingWiki 或商店文本提取，可为空）
    horror_level TEXT DEFAULT 'none',       -- 'none'(不恐怖) / 'mild'(微恐/心理恐怖) / 'scary'(重度恐怖)
    review_score INTEGER DEFAULT 0,         -- 0~9 对应九档评价（全语言）
    review_tier_zh TEXT DEFAULT '评测不足',
    total_reviews INTEGER DEFAULT 0,        -- 总评测数
    positive_pct REAL DEFAULT 0.0,          -- 全语言好评率 (0~100)
    schinese_score INTEGER,                 -- 简中区九档枚举 (1~9，可为空)
    schinese_pct REAL,                      -- 简中区好评率 (0~100，可为空)
    schinese_total INTEGER,                 -- 简中区评测数
    short_desc TEXT,                        -- 简短介绍
    categories_json TEXT,                   -- JSON 数组，存中文类别名
    tags_json TEXT,                         -- JSON 数组，存前10个中文标签名
    avoid_reasons_json TEXT,                -- JSON 数组，汇总差评高频雷点（如 ["掉线频繁(5条提及)", "流程太短(3条提及)"]）
    is_avoid INTEGER DEFAULT 0,             -- 1=进入避雷榜
    header_image TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS game_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appid INTEGER NOT NULL,
    recommendation_id TEXT,                 -- 用于拼接原文链接
    voted_up INTEGER NOT NULL,              -- 1=好评, 0=差评
    votes_helpful INTEGER DEFAULT 0,        -- 有用票数
    playtime_hours REAL DEFAULT 0.0,        -- 评测时游戏时长（小时）
    quote_text TEXT NOT NULL,               -- 原文精确子串（≤80字）
    dimensions_json TEXT,                   -- 命中的维度标签，如 ["coop_quality", "network"]
    review_url TEXT NOT NULL,               -- https://steamcommunity.com/profiles/.../recommended/<appid>/
    FOREIGN KEY (appid) REFERENCES games(appid)
);

CREATE INDEX IF NOT EXISTS idx_games_filter ON games(online_coop, horror_level, is_free, has_demo, review_score DESC);
CREATE INDEX IF NOT EXISTS idx_quotes_appid ON game_quotes(appid, voted_up);
```

#### Step 1.3：数据抓取与入库流程（`scripts/build_snapshot.py`）

编写命令行脚本 `python scripts/build_snapshot.py --limit 200`，按以下 4 步流水线执行：

1. **拉取候选 AppID 池（通过 `search/results`）**：
   - 路 A（热门在线合作正式游戏）：`category1=998&category3=38&sort_by=Reviews_DESC&count=100&infinite=1`（翻 2 页拿 Top 200）。
   - 路 B（合作游戏 Demo 池）：`category1=10&category3=38&sort_by=Reviews_DESC&count=100&infinite=1`（拿 Top 100 联机 Demo）。
   - 路 C（恐怖联机专项标记池）：`category1=998&category3=38&tags=1667&count=100&infinite=1`，记录所有命中的 `appid` 放入 `horror_appid_set`。
   - **直接从搜索结果 HTML 提取初筛字段**：用 BeautifulSoup 解析每行 `<a class="search_result_row">`，提取 `data-ds-appid`、`data-ds-tagids`、标题、价格，以及 `data-tooltip-html`（用正则 `r"^(.*?)<br>.*?([\d,]+)\s*篇.*?(\d+)%"` 直接拿到中文档位、总评测数、全语言好评率，省去大量额外请求）。
2. **补充单游戏详情与商店页（控制速率：间隔 1.5s，带本地文件缓存）**：
   - 调用 `/api/appdetails?appids=<id>&cc=cn&l=schinese`，提取：`type`、`is_free`、`price_overview`（`final / 100.0` 转元）、`categories`、`demos`、`fullgame`、`short_description`。
   - 请求商店页 `/app/<id>/?l=schinese&cc=cn`（同时携带年龄绕过 Cookie：`birthtime=0; mature_content=1; lastagecheckage=1-0-1990`）：
     - 解析 `InitAppTagModal(<id>, [...])` JSON，获取该游戏完整的中文标签与票数。若标签含 `1667/3978` 设为 `horror_level='scary'`；若不含 `1667` 但含 `1721`（心理恐怖）或标签票数排名靠后，设为 `'mild'`；否则 `'none'`。
     - 解析 `data-featuretarget="appreviews"` 的 `data-props`（先 `html.unescape` 再 `json.loads`），从 `filter_options.rgLanguageOutliers` 中提取 `strLanguage == "schinese"` 的简中专属好评率与档位。
3. **拉取中文评测原文（当网络/Actions 可访问 `appreviews` 时）**：
   - 好评请求：`/appreviews/<id>?json=1&language=schinese&filter=all&review_type=positive&playtime_filter_min=1&num_per_page=20`
   - 差评请求：`/appreviews/<id>?json=1&language=schinese&filter=all&review_type=negative&playtime_filter_min=1&num_per_page=20`
   - **子串抽取与防幻觉校验（`extractor.py`）**：
     - 将每条评测按中文标点（`。！？\n`）切分为句子，过滤掉少于 6 个字或超过 80 个字的句子。
     - 用 `REVIEW_KEYWORDS` 匹配句子，按 `命中维度数 × log(votes_up + 2)` 排序，每款游戏最多保留 **3 条好评原句 + 2 条差评原句**。
     - **硬性校验**：入库前执行 `assert quote_text in raw_review_text`，并拼接真实可点击链接 `https://steamcommunity.com/profiles/{author_steamid}/recommended/{appid}/?snr=1_5_100010_`（或通过 `recommendationid` 跳转）。
   - **生成避雷标记（`is_avoid`）**：
     - 若某游戏符合以下任一条件，标记 `is_avoid = 1` 并汇总 `avoid_reasons_json`：
       1. 全语言档位 ≤ 5（褒贬不一及以下）；
       2. 全语言档位 ≥ 7，但简中好评率比全语言低 15% 以上（典型如无官中、国内服务器连不上）；
       3. 抓取到的 Top 20 中文差评中，有 ≥ 3 条同时命中 `network`（掉线/连不上）或 `dealbreaker`（外挂/半成品/假联机）。
4. **（可选）调用 PCGamingWiki 补充联机人数**：
   - 对 Top 100 游戏查询 `https://www.pcgamingwiki.com/w/api.php?action=opensearch&search=<英文名>` 拿到 page title，再调 `action=parse&prop=wikitext` 正则提取 `online play players = (\d+)`，写入 `max_players`。

---

### Phase 2：实现 FastMCP Server（`src/coop_night_picker/server.py`）

使用官方 `mcp` SDK 的 `FastMCP` 实现。核心设计是**「两段式追问状态机」**：当用户在第一轮只说「今晚想玩联机游戏」时，工具发现缺少核心筛选参数，直接返回结构化的追问指令，引导客户端 LLM 向用户提问。

#### Step 2.1：4 个核心 Tool 完整签名与逻辑

```python
from typing import Optional, Literal
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("今晚玩什么 (coop-night-picker)")

@mcp.tool()
def recommend_coop_games(
    horror: Optional[Literal["none", "mild", "scary", "any"]] = None,
    budget: Optional[Literal["free_or_demo", "under_50", "any_paid", "any"]] = None,
    players: Optional[int] = None,
    mode: Literal["online", "local", "any"] = "online",
    confirmed: bool = False,
    limit_per_tier: int = 3,
) -> dict:
    """
    【核心工具】推荐今晚和朋友玩的 Steam 联机游戏，按 Steam 九档评价分组并附带真实评测理由与避雷榜。
    
    重要调用规则：
    1. 如果用户还没有明确说明【是否接受恐怖 (horror)】或【想玩免费Demo还是付费买 (budget)】，
       且 confirmed=False，请直接以当前已知参数调用本工具。工具会返回 need_more_info 和需要追问的问题列表。
    2. 收到 need_more_info 后，请把问题合在一轮对话里简短问完用户（不要自己乱猜参数）。
    3. 用户回答后，带上具体参数并将 confirmed=True 再次调用本工具，即可获得九档榜单。
    """
    # 1. 检查是否需要追问一轮
    missing_questions = []
    if horror is None and not confirmed:
        missing_questions.append({
            "param": "horror",
            "question": "恐怖游戏能接受吗？",
            "options": ["一点都不行 (none)", "微恐/心理恐怖可以 (mild)", "越吓人越好 (scary)", "都行 (any)"]
        })
    if budget is None and not confirmed:
        missing_questions.append({
            "param": "budget",
            "question": "想白嫖免费游戏/试玩 Demo，还是愿意花钱买？",
            "options": ["只看免费或有免费Demo的 (free_or_demo)", "50元以内便宜好玩的 (under_50)", "价格无所谓，好玩就行 (any)"]
        })
    if players is None and not confirmed:
        missing_questions.append({
            "param": "players",
            "question": "今晚大概几个人一起玩？（比如 2 人、4 人）",
            "options": ["2人", "3-4人", "5人以上"]
        })

    if missing_questions:
        return {
            "status": "need_more_info",
            "instruction_for_assistant": (
                "请用轻松自然的语气，在【一轮回复】内向用户追问以下问题。"
                "用户回答后，请传入对应参数并设置 confirmed=True 重新调用 recommend_coop_games。"
            ),
            "questions": missing_questions,
        }

    # 2. 参数齐全：查询本地 SQLite（自动剔除 user_log.json 中已标记 played / avoided 的游戏）
    # 3. 将满足条件且 is_avoid=0 的游戏按 review_score (9 -> 1) 分组，每档取前 limit_per_tier 款
    # 4. 将满足条件但 is_avoid=1（或差评率高）的同类型热门游戏放入 avoid_list（最多 3 款，作为避雷提醒）
    # 5. 同时生成一份排版好的 markdown_summary 字段，方便客户端直接展示
    ...
```

其余 3 个配套 Tool：
- **`get_game_card(appid: int)`**：查询单款游戏的完整证据卡（全语言九档 + 星级 vs 简中好评率对比、是否含免费 Demo、联机方式、正反方玩家评测原话及原文 URL）。
- **`resolve_game(query: str, limit: int = 5)`**：先查本地 SQLite `name LIKE %query%`，若不足则调用 `steam_client.store_search(query)` 实时返回候选 `appid` 列表。
- **`log_play(appid: int, status: Literal["played", "avoided", "wishlist"], note: str = "")`**：将用户及朋友试玩后的反馈（如 `"avoided": "垃圾Demo，进去5分钟就掉线"`）写入 `data/user_log.json`，后续调用 `recommend_coop_games` 时自动排除。

#### Step 2.2：输出榜单的 Markdown 渲染规范

在 `recommend_coop_games` 返回字典中，除了结构化 `groups` 和 `avoid_list` 外，附带 `markdown_preview`，固定格式如下：

```markdown
### 🎮 今晚玩什么 · 筛选结果（4人｜不恐怖｜免费/Demo优先）

#### ★★★★★ 好评如潮（Score 9）
1. **《深岩银河》(Deep Rock Galactic)** — ¥32.4 (-70%) | 支持 1-4 人在线合作
   - 📊 **口碑**：全语言 97.3%（好评如潮，30.4万篇）｜简中 93.2%
   - 💡 **推荐理由（来自玩家评测）**：
     > "和三个朋友挖矿打虫子太快乐了，一局25分钟节奏刚好，矮人永不抛弃队友！"（👍 128 人觉得有用 · [查看原评](https://steamcommunity.com/...)）

#### ★★★★½ 特别好评（Score 8）
...

#### ☆ 试玩友好 / 新品 Demo 区（评测 < 50 篇）
...

---
#### ⚠️ 避雷榜（同筛选条件下容易踩坑的游戏）
- ❌ **《XX联机版》** — 褒贬不一 (52%)
  - **劝退原因**：12 条中文差评提到「服务器掉线」「匹配不到人」
  - > "跟朋友联机五分钟掉线三次，纯纯坐牢，快逃"（[原评链接](https://...)）
```

---

### Phase 3：GitHub Actions 海外自动更新快照（`.github/workflows/update_snapshot.yml`）

为了彻底绕开国内对 `appreviews` 的封锁，配置 GitHub Actions 每天或每周在海外 Runner 上自动跑一次抓取脚本，并将更新后的 `data/games.sqlite` 提交回仓库（或发到 Release）：

```yaml
name: Update Steam Co-op Snapshot

on:
  schedule:
    - cron: '0 19 * * 4'   # 每周五凌晨 03:00 (北京时间)，赶在周末开黑前更新
  workflow_dispatch:       # 支持手动一键触发

jobs:
  build-db:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install dependencies
        run: pip install httpx beautifulsoup4 lxml
      - name: Build and verify SQLite snapshot
        run: |
          python scripts/build_snapshot.py --limit 300 --with-reviews
          python scripts/verify_snapshot.py
      - name: Commit updated database
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/games.sqlite
          git diff --staged --quiet || git commit -m "chore(data): update steam coop snapshot"
          git push
```

---

### Phase 4：客户端接入与本地验收测试清单

代码生成完毕后，按以下顺序验收：

1. **单元测试与快照校验**：
   - 运行 `python scripts/verify_snapshot.py`，确保：
     - `games` 表记录数 ≥ 50，且同时包含 `is_free=1`、`has_demo=1`、`horror_level='scary'`、`horror_level='none'` 的样本。
     - `review_score` 覆盖多个档位，且 `review_tier_zh` 与九档表严格一致。
     - 所有 `game_quotes` 的 `quote_text` 非空，且 `review_url` 以 `https://steamcommunity.com/` 开头。
2. **MCP Inspector 交互验收**：
   - 运行 `mcp dev src/coop_night_picker/server.py`
   - **测试 Case 1（无参追问）**：调用 `recommend_coop_games()` 不传参 → 验证返回 `status == "need_more_info"` 且包含 3 个追问问题。
   - **测试 Case 2（不恐怖 + 免费/Demo）**：调用 `recommend_coop_games(horror="none", budget="free_or_demo", players=4, confirmed=True)` → 验证返回结果中无任何 `horror_level == "scary"` 的游戏，且按星级从高到低分组，底部带 `avoid_list`。
   - **测试 Case 3（拉黑踩雷游戏）**：调用 `log_play(appid=..., status="avoided", note="太无聊")`，再重新调用 Case 2 → 验证该 `appid` 已从推荐列表中消失。
3. **接入 Claude Desktop / Cursor / Cherry Studio**：
   ```json
   {
     "mcpServers": {
       "coop-night-picker": {
         "command": "python",
         "args": ["-m", "coop_night_picker.server"],
         "env": {
           "PYTHONPATH": "你的项目绝对路径/src"
         }
       }
     }
   }
   ```

---

## 五、 直接复制给 GPT / DeepSeek 的执行 Prompt

当你把本文件和修订后的调研报告一起发给 GPT 或 DeepSeek 时，附上下面这段话即可让它直接开工：

```text
请仔细阅读我提供的《TECH-PLAN-今晚玩什么-技术路径.md》和《REPORT-今晚玩什么-MCP调研报告.md》(v1.1)。
现在请你作为资深 Python 工程师，严格按照《技术路径》第三节的目录结构和第四节的 Phase 0 → Phase 2，分步为我输出完整、可直接运行的项目代码（不要用省略号跳过核心逻辑）：

第一步（本轮输出）：
1. `pyproject.toml`
2. `src/coop_night_picker/constants.py`（严格使用勘误后的九档评价枚举和关键词正则）
3. `src/coop_night_picker/db.py`（含建表、upsert、按条件筛选分组查询、避雷榜查询）
4. `scripts/probe_network.py`（含直连/代理/CDN 四路探测与 302 拦截检测）

等我确认第一步代码运行通过后，我再让你输出第二步（`steam_client.py`、`extractor.py`、`build_snapshot.py`）和第三步（`server.py` 与测试脚本）。
```
