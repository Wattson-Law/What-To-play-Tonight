# 「今晚玩什么」MCP Server — 可行性调研报告

> 调研时间：2026-10-28 ｜ 调研者：DeepSeek Harness Agent Teams（Lead + 4 个调研 teammate）｜ **v1.1 勘误：2026-10-28（见下方勘误表）**
> 调研对象：为 Steam 联机游戏做"按评价档位分组 + 带真实评测理由"的推荐 MCP Server，Python/FastMCP 实现
> 证据规则：**所有接口行为、ID、字段、返回码都由本机在 2026-10-28 实际请求验证**，原始响应留档于 `research/raw/`；社区讨论与文档均附链接。文中 **【事实】** = 有抓取/实测证据；**【推断】** = 我的判断，未验证。

## v1.1 勘误表（2026-10-28，Antigravity 复核）

> 原文中被修改处均以 **【v1.1】** 标注；原始版本已备份为 `REPORT-今晚玩什么-MCP调研报告.v1.0.bak.md`。后续实施以《TECH-PLAN-今晚玩什么-技术路径.md》为准。

| # | 位置 | 问题 | 修正 |
|---|---|---|---|
| 1 | 附录"九档映射" | 写成 `8=好评如潮(II)`、`7=特别好评`，与 §3.4 正文表格矛盾 | 以 §3.4 为准：9 好评如潮 / 8 特别好评 / 7 好评 / 6 多半好评 / 5 褒贬不一 / 4 多半差评 / 3 差评 / 2 特别差评 / 1 差评如潮 |
| 2 | §3.4 星级列 | 8 档与 7 档都是 4 颗星，无法区分 | 统一为半星步进：9→5、8→4.5、7→4、6→3.5、5→3、4→2.5、3→2、2→1.5、1→1 |
| 3 | §3.2 / 附录 | `appdetails` 用了 `l=zh` | Steam 语言参数应为 `l=schinese`；"12/16 个 categories 返回英文"很可能就是这个原因（待实测） |
| 4 | §3.1 / §8.2 | "官方域名全线不可用" | 只在未走代理的网络下成立。用户本机经代理 `127.0.0.1:7890` 实测 `store.steampowered.com/api/appdetails` 返回 200；评测接口失败最可能是代理规则把 Steam 设为直连 |
| 5 | §3.1 示例 / §8.3 / 附录候选池 | `category1=9`、`category1=38` | `category1` 是"类型"（998=游戏，10=Demo），联机类别应放 `category3`（如 `category3=38` 在线合作），或改用标签 `tags=3843`。**待实测** |
| 6 | §8.3 Day 1 | `appreviews?appids=548430` | 正确写法 `/appreviews/548430?json=1...`，appid 在路径里 |
| 7 | 附录"重要提醒" | `downloadableContent` 只给 Top-10% 游戏差评 | 无来源且与文档不符，已删除；`appreviews` 支持 `review_type=negative` 直接拉差评 |
| 8 | 附录"Requests 正确姿势" | LibreSSL 3.5.2 / curl 才能通 | 不适用于用户的 Windows + Python 3.12 环境（本机 `urllib`/`httpx` 经代理可通），改为通用建议 |
| 9 | §5.2 | "2026-07-28 规范取消服务端反向请求（MRTR）"、"ctx.elicit 只在旧连接可用" | 标为【待核实】；不影响设计，主干仍用"缺参返回追问问题" |
| 10 | §3.4 阈值 | 只给了好评率，没提评测数 | 补全社区口径：档位同时取决于好评率和评测数量 |
| 11 | §8.3 vs §0 | "72 小时"与"2–3 周 MVP"矛盾 | 72 小时 = 可演示原型；完整 MVP 仍是业余 2–3 周 |
| 12 | §8.4 | 称 §2 需求调研"未落盘" | §2 实际已有 Reddit 9 条 + B 站 4 条原文，仅知乎/贴吧/小黑盒只有标题 |
| 13 | 架构 | 只在本地抓数据，评测原文无解 | **新增方案：海外 GitHub Actions 定时抓取（含评测原文）→ SQLite 快照 → 本地 MCP 只读**，见技术路径 |


---

## 0. 结论先行（TL;DR）

| 问题 | 答案 |
|---|---|
| 值不值得做？ | **值得做，但不要做"又一个 Steam 推荐器"**。MCP 生态里联机推荐这一格是空的（§1），而"场景追问 + 九档分组 + 中文真实评测理由 + 避雷榜"是现有 4 个 steam-mcp 都没有的（§1.3）。 |
| 最大的风险 | **不是技术，是数据访问**：Steam 官方域名（`store.steampowered.com` / `api.steampowered.com` / `steamcommunity.com`）在国内是 **TLS/SNI 层阻断且间歇发作**（§3.1）；更要命的是 `appreviews` **即使换到 Akamai CDN 主机也被 302 重定向拒绝**（0 字节，§3.4）。产品价值 70% 来自评测，而评测是**唯一拿不到的那个**——但有 3 条替身通路能拿到"中文九档 + 好评率"（§3.4）。**【v1.1】该结论只在"未走代理 / 代理规则把 Steam 设为直连"的网络下成立；最终方案改为"海外 GitHub Actions 抓取 + 本地 SQLite 快照"，见《技术路径》。** |
| 第一步该做什么 | **今天先做一件事：验证你目标网络里 `appreviews` 能不能通**（§3.4 的 302 现象 + §8.2 的三条路）。通 → 按 MVP 开工；不通 → 走"商店页/搜索 tooltip 拿九档 + 离线快照"的降级版（§8.2①b/c），照样能做出可用产品。 |
| MVP 多久能出 | 【推断】4 个 tool + 1 个 prompt，业余 2–3 周可出可演示版；难点全在缓存与限流，不在业务逻辑。 |
| 中文名能用吗 | 能用（App Store/各平台无冲突），但 GitHub 上已有一个语义撞车的 `tinman00/tonight`（§7.1）。建议仓库名 `coop-night-picker`。 |

---

## 1. 竞品与现状

### 1.1 A1：MCP 生态里的 Steam Server（GitHub / Smithery / mcp.so / Glama）

| 项目 | 链接 | ★ | 最近更新 | 语言/分发 | 功能 | 缺陷（相对本产品） |
|---|---|---|---|---|---|---|
| **Sarg338/steam-mcp** | github.com/Sarg338/steam-mcp | 7 | 2026-09-24 | Python / PyPI（30 天 1504 次下载） | **41 个 tool**：库、成就、价格、商店搜索、评测、玩家数，已有 `steam_plan_coop_night`（按好友共同拥有 + 联机标签做"联机之夜"规划） | 需要 Steam Web API Key + SteamID；**无按评价档位分组**、无中文评测理由、无避雷榜；工具太多，模型选择成本高 |
| **0x216/imho-mcp** | github.com/0x216/imho-mcp | 1 | 2026-10-06 | TS / 托管 imho.run/mcp | 托管式"游戏值不值得买"：聚合 Steam/OpenCritic/Metacritic 评分 | 不是联机场景；无 Demo/免费维度；托管服务有稳定性与隐私问题 |
| **TMHSDigital/steam-mcp** | github.com/TMHSDigital/steam-mcp | 5 | 2026 年内 | TS / npm（82 次/月） | 商店搜索、详情、价格 | 功能薄；无推荐逻辑 |
| **dsp/mcp-server-steam** | github.com/dsp/mcp-server-steam | 26 | 2025-02 后停更 | TS | 库/价格/新闻 | **已停更一年半**，无推荐 |
| **fenxer/steam-review-mcp** | github.com/fenxer/steam-review-mcp | 7 | 2026 年内 | Python | 拉某游戏评测并总结 | 只做"单游戏评测总结"，不解决"今晚玩什么" |
| **Praeses0/…**（steam 相关） | GitHub | 14 | 2026 年内 | — | Steam 数据工具 | 无场景推荐 |

**目录侧现状（【事实】2026-10-28 抓取）**：Smithery 搜 `steam` 命中 10 个、mcp.so 36 个、Glama 30+ 个；GitHub 搜 `steam mcp` 共 **214** 个仓库，但 `game recommendation mcp` 仅 **6** 个、`coop game mcp` 仅 **5** 个。
→ **【推断】"Steam 数据类 MCP"已经红海，"联机游戏决策类 MCP"仍是空白。** 这是本项目最重要的正面信号。

### 1.2 A2：非 MCP 的同类产品

| 名称 | 链接 | 类型 | 功能 | 缺陷 |
|---|---|---|---|---|
| **Co-Optimus** | co-optimus.com | 老牌联机游戏数据库 | 按联机人数/模式筛选，人工维护 | 【事实】本机抓取返回 **403**；界面古老、无中文、无评价档位 |
| **SteamDB** | steamdb.info | 数据站 | 实时在线人数、价格史、标签筛选 | 无"推荐理由"；无中文评测；反爬严格（本机被 reset） |
| **SteamPeek** | steampeek.hu | 相似推荐 | 按标签找相似游戏，有 co-op 标签过滤 | 【事实】可访问；只做"相似"，不做"今晚玩什么" |
| **双人游戏网 / coopgaming.games** | coopgaming.games | 中文联机站 | 双人/多人游戏清单 | 人工榜单、更新慢、无个性化 |
| **B 站 / 小红书 / NGA 联机游戏 UP 主** | — | 内容 | "周末联机游戏推荐""避雷"视频/笔记 | 时效性强但**不可交互**，无法按"人数/恐怖/预算"即时筛 |
| **Steam 自己的"探索队列"/标签页** | store.steampowered.com | 官方 | 标签 + 评价筛选 | 不能按"我们 4 个人今晚 2 小时"筛；无跨游戏对比 |

### 1.3 A3：差异化（可写进 README 的三句话）

1. **场景收窄**：不问"推荐游戏"，只问"今晚我们 4 个人、不玩恐怖、只想白嫖 Demo、玩两小时"——竞品没有这个入口。
2. **九档分组 + 星级**：直接把 Steam 的 `review_score` 九档做成榜单骨架，用户一眼知道"这榜里最好的是 9 档"。竞品要么不分档，要么只给一个百分比。
3. **真实中文评测理由 + 避雷榜**：每条推荐附 1–3 条可点开的简中评测原话（含"掉线/外挂/内容少"），并单独给"避雷榜"。**Sarg338 有 41 个 tool，但全是正向数据，没有负向榜单。**
4. **零 Key 起步**：【事实】商店接口不需要 API Key（只有 Web API 需要），所以"装完就能用"是可能的——这直接决定转化率。

> 注意（不要重复造）：搜索/价格/相似推荐/好友库联动/评测抓取，这些**已经有人做了**（Sarg338 最全）。你的护城河只能是"决策层"（场景 → 分档 → 理由 → 避雷），不是"数据层"。

---


## 2. 需求验证：社区真实声音（Reddit / 贴吧 / 知乎 / NGA / B 站）

- 调研日期：2026-10-28｜调研员：demand-scout｜任务板 task-2
- 语料：Reddit（r/CoopGaming）25 条主帖原文 + B 站 10 条高赞评论原文 + 中文搜索结果标题/摘要 53 条（约 3.1 万字符）
- **数据来源与可达性（重要，影响每条证据的"可核验级别"）**
  - `reddit.com` / `old.reddit.com` / 多个 redlib 镜像：**直连全部超时**（本机网络不可达）。Reddit 原文改用 Reddit 存档 API `api.pullpush.io` 获取（返回 `title`/`selftext` 原文），链接给出官方 permalink；**permalink 本身本次打不开，但正文是 API 返回的原文，非改写**。
  - `www.zhihu.com`、`tieba.baidu.com` 页面：**403**，只能拿到搜索引擎给出的标题/摘要。
  - `bilibili.com`：**可打开**（视频页与评论 API 均可用）。`bbs.nga.cn` TLS 重置、`ngabbs.com` 403；`api.xiaoheihe.cn` 分享页可打开。
  - 检索通道：DuckDuckGo / Google / r.jina.ai 不可达；Bing 英文索引返回无关结果（"Deep Rock Galactic co-op review" → DeepSeek/DeepL）；**So.com（360）与 Sogou 中文检索可用**，Sogou 连续请求触发验证码 → 改用 So.com + `site:` 定向。

---

### ① 英文证据（Reddit，原文摘录为 API 返回的 title/selftext 原样）

| # | 平台 | 标题（原文） | 原文短摘录（≤2 句） | 链接 | 日期 | 打开原文？ |
|---|---|---|---|---|---|---|
| E1 | r/CoopGaming | Does anyone else spend more time organizing co-op sessions than actually playing? | "For my group it's always the same loop: * someone doesn't own the game * someone's not free * we spend 15-30 minutes just figuring out what to play and when" | https://www.reddit.com/r/CoopGaming/comments/1sn9mo3/ | 2026-04-16 | 否（permalink 超时，正文经 pullpush 取得） |
| E2 | r/CoopGaming | Anyone else quietly anxious you're wasting your rare game nights? | "we burn 30+ minutes talking about what to play. 'Deep Rock?' 'I'm burned out on that.' 'Phasmo?' 'I don't want to reinstall that'" | https://www.reddit.com/r/CoopGaming/comments/1rfm1nq/ | 2026-02-26（22 赞） | 否（同上） |
| E3 | r/CoopGaming | Looking for Coop-Survival | "2 friends of mine and me doing Lan-Partys on a regular basis. Now we are coming to a problem that we are running out of ideas what to play." | https://www.reddit.com/r/CoopGaming/comments/1ux6xxw/ | 2026-07-15（32 评论） | 否（同上） |
| E4 | r/CoopGaming | My friends and I could never agree on what co-op game to play, so I built a swipe tool for it | "Every game night went the same way: everyone's in the Discord call, and then we spend 40 minutes arguing about what to play." | https://www.reddit.com/r/CoopGaming/comments/1wwv6f2/ | 2026-10-03 | 否（同上） |
| E5 | r/CoopGaming | Any good co-op games with a campaign? | "the internet is bad where I live, so please avoid online games … We prefer shooters and horror, but any genre is fine." | https://www.reddit.com/r/CoopGaming/comments/1uar3t1/ | 2026-06-20 | 否（同上） |
| E6 | r/CoopGaming | Any games like It takes two, but horror? | "Any couch coop games or like split screen games that me and my gf can play? … we now don't know what to play." | https://www.reddit.com/r/CoopGaming/comments/1qf9g2i/ | 2026-01-17（16 评论） | 否（同上） |
| E7 | r/CoopGaming | Finding new games to play with my gf | "my gf and I are looking for our next challenge. We're not too picky but we cannot think of what to play next." | https://www.reddit.com/r/CoopGaming/comments/1mcankc/ | 2025-07-29（113 赞 / 158 评论） | 否（同上） |
| E8 | r/CoopGaming | Beta testers wanted for a co-op game finder I built for myself and my friends | "• finding co-op games that actually look fun for the group • deciding what to play without endless back-and-forth • sometimes needing more people to play with • figuring out whether a game will actually run on someone's PC" | https://www.reddit.com/r/CoopGaming/comments/1s1v8f3/ | 2026-03-23 | 否（同上） |
| E9 | r/CoopGaming | I made a site to help you choose what to play with friends | "We ended up sitting in discord for ages arguing about what to play. … had over 400 people create sessions through it." | https://www.reddit.com/r/CoopGaming/comments/1uq4ctp/ | 2026-07-07（17 赞） | 否（同上） |

> 说明：r/Steam、r/patientgamers、r/mcp 的定向检索（pullpush `subreddit=Steam/patientgamers/mcp` 关键词 `"2 player" co-op`、`co-op friend`、`demo refund`、`game`）**全部被 429 限流拦住，本次未取到**，写"未找到（限流）"，不臆造。

**由 E1–E9 可直接读到的事实**：同一子版在 2026 年内出现多条"组织成本 > 游玩成本""一晚上 30–40 分钟在选游戏"的帖子；且有至少 3 个独立作者因为这个问题自己造工具（coopswipe / steamr.io / co-op.now / SquadRoll），其中 E9 自报"400+ 人创建过 session"。

---

### ② 中文证据

| # | 平台 | 标题 | 原文短摘录（原样，≤2 句） | 链接 | 日期 | 打开原文？ |
|---|---|---|---|---|---|---|
| C1 | B 站评论区（双人/多人游戏推荐，293.7 万播放） | 全网最全 一网打尽！双人游戏/多人游戏推荐！ | "你以为的高配置：上万的配件 实际上的高配置：稳定的游戏朋友"（6974 赞） | https://www.bilibili.com/video/BV1tR4y1i73q/ | 评论可见，视频较旧 | **能** |
| C2 | B 站评论区（Steam 远程同乐攻略） | 【Steam远程同乐】2025年最新最全使用干货攻略 | "远程同乐最离谱的就是，如果两个人在一个家庭组里，必须得两个人同时买游戏才能进行远程同乐，否则就会提示家庭库已锁定，非常的离谱"（336 赞） | https://www.bilibili.com/video/BV1JD6FYgEbx/ | 2025 | **能** |
| C3 | B 站评论区（同上） | 同上 | "和朋友都开了加速器，我们两个人邀请对方远程畅，进入后显示tip过了几秒就会被弹出来"（287 赞） | https://www.bilibili.com/video/BV1JD6FYgEbx/ | 2025 | **能** |
| C4 | B 站评论区（双人联机手游推荐，191 万播放） | 【双人联机手游推荐】快叫上你的女（好）朋（基）友一起吧！！！ | "省流:没有手机游戏适合情侣联机"(1623 赞) | https://www.bilibili.com/video/BV1rr4y1Y7j2/ | — | **能** |
| C5 | B 站视频 | 【steam推荐】多人+联机+免费=无脑买买买??小心这些会踩雷的独立游戏! | 标题即结论："多人+联机+免费"被当作购买理由，创作者专门做"踩雷"警示（标题原文） | https://www.bilibili.com/video/BV1yq4y1q7Ti/ | 2021-12-11 | **能**（未取字幕） |
| C6 | B 站视频 | 试玩结束了，好多BUG | 标题原文（"试玩→bug→劝退"的典型表述） | https://www.bilibili.com/video/BV1ubTozLEs9/ | — | **能**（未取字幕） |
| C7 | B 站视频 | steam远程同乐不稳定？低配朋友联机体验差？试试这种新型联机方法 | 标题原文：同时点出"网络不稳"+"朋友机子低配"两个筛选维度 | https://www.bilibili.com/video/BV18kGXzmEpW/ | — | **能**（未取字幕） |
| C8 | 百度贴吧 · steam吧 | 大佬们,想跟朋友一起联机玩,有没有什么好的游戏推荐?沙盒,开放性世界得最好,4个人,pve和pvp均衡一下 | 标题原文即完整筛选条件：人数=4、类型=沙盒/开放世界、PVE/PVP 配比 | https://tieba.baidu.com/p/6232909340 | — | 否（403，仅标题） |
| C9 | 百度贴吧 · steam吧 | 有什么好玩的联机游戏推荐呢,适合和朋友开黑的。吃鸡和csgo就算了,我和室友已经被虐够了 | 标题原文：带**排除项**（竞技类不要）+ 同住室友场景 | https://tieba.baidu.com/p/6423068738 | — | 否（403，仅标题） |
| C10 | 百度贴吧 · steam吧 | 问一下有什么双人联机的游戏 | 标题原文（人数=2 的最短真实需求） | https://tieba.baidu.com/p/6002612504 | — | 否（403，仅标题） |
| C11 | 知乎 | steam上推荐什么多人联机游戏? | 标题原文（问题存在即需求；含大量回答） | https://www.zhihu.com/question/309201704 | — | 否（403） |
| C12 | 知乎 | 有什么好玩双人联机的游戏? | 标题原文 | https://www.zhihu.com/question/344377089 | — | 否（403） |
| C13 | 小黑盒 | [Steam游戏推荐]20款适合跟情侣/朋友一起玩的小众联机游戏 | 标题原文："情侣/朋友"+"小众"（避开人尽皆知的 3A） | https://api.xiaoheihe.cn/maxnews/app/share/detail/2600855 | 2026-04-06 | 否（分享页需 App，仅标题） |
| C14 | 小黑盒 | 便宜好玩的本地多人游戏推荐第三期(完结) | 标题原文：价格（便宜）+ 同屏/本地（本地多人） | https://api.xiaoheihe.cn/maxnews/app/share/detail/2431205 | 2023-04-18 | 否（仅标题） |
| C15 | 小黑盒 | 【游戏推荐】双人异地联机游戏推荐 | 标题原文：人数=2 + **异地**（=网络质量维度） | https://api.xiaoheihe.cn/maxnews/app/share/detail/2223158 | 2024-08-31 | 否（仅标题） |
| C16 | 小红书 | 两人开发,2天赚了3000万,Steam新晋榜一 | 仅标题可见，与筛选维度关系弱 | https://www.xiaohongshu.com/explore/6a8cf6d0000000002a004f77 | 2026-10-11 | 否（需登录） |
| C17 | NGA | （`site:nga.cn` 检索只返回论坛首页与资讯页，未命中具体楼） | — | — | — | **未找到** |

> 未使用/已排除：小红书"双人联机"类结果多为抖音/今日头条聚合页（非平台原帖），未列入；贴吧"踩坑"检索命中的是《饥荒》《恐怖黎明》等单机新手攻略，与"联机试玩踩雷"不对题，未列入。

---

### ③ 用户最在意的筛选维度（频次 + 判读）

统计方法：对本次语料（Reddit 25 帖 title+selftext、B 站 10 条评论、中文搜索结果标题/摘要 53 条，共约 31.6k 字符）做关键词命中计数；关键词命中数**含噪声**（如"网络"会命中原神/加速器广告），故附人工判读列。

| 排序 | 筛选维度 | 关键词命中 | 人工判读（是否真·决策变量） | 典型证据 |
|---|---|---|---|---|
| 1 | **人数 / 队形**（2 人、3–4 人、是否同屏、上限几人） | 54 | 最硬的条件，几乎所有提问第一句就限定人数 | E6、E3、C8、C10、C15 |
| 2 | **价格 / 免费 / 是否要再买一份**（含"朋友要不要也买"） | 40（"是否双方都买"单列 3） | 高频且直接影响成交；"远程同乐要两人都买"是明确痛点 | C2、C14、E1（someone doesn't own the game） |
| 3 | **一晚能不能玩起来**（时长、上手门槛、内容量、存档/进度） | 17 | 帖子反复出现"rare game nights""30–40 分钟在选游戏"，反推游戏要"当晚就能开局" | E1、E2、C4 |
| 4 | **类型与氛围**（恐怖与否、PVE/PVP、竞技 vs 合作、沙盒/生存/解谜） | 恐怖 15 + 类型词大量 | 恐怖是被单独点名的氛围轴（"不要恐怖""就要恐怖"两极） | E5、E6、C8、C9（排除吃鸡/CSGO） |
| 5 | **网络质量**（在线 vs 本地/远程同乐、延迟、加速器、服务器） | 44（含噪声） | 异地问朋友、低配朋友、远程同乐掉线都会直接毁掉一晚 | C2、C3、C7、E5（"internet is bad…avoid online games"） |
| 6 | **配置 / 性能**（朋友电脑能不能跑） | 13 | 常以"低配朋友"形式出现，会筛掉一半候选 | E8、C7 |
| 7 | **Demo / 试玩口碑**（试玩踩雷、新品节、先试再买） | 14 | 痛点明确但目前靠"看 UP 主评测"而非结构化数据解决 | C5、C6 |
| 8 | **中文 / 语言支持** | 4 | 本次语料中出现频率最低；可能是"默认有官中"后的沉默项 | 无强证据，【推断】低优先 |
| 9 | **凑得齐人 / 好友库重叠**（有没有 3 个朋友在玩、谁已经买了） | 6 | 数量不高但语义最重：它是"推荐完也玩不成"的根因 | E1、E8、C1（"稳定的游戏朋友"6974 赞） |

**排序结论（推断）**：`人数 + 价格/是否双方都要买 + 当晚可玩性` 是筛选的第一层硬约束（不满足直接出局）；`恐怖/类型氛围 + 网络 + 配置` 是第二层；`中文` 是第三层（沉默默认项）。

---

### ④ 结论

#### 需求真实性

- **【事实】** r/CoopGaming 在 2025-07 至 2026-10 期间，至少有 9 条帖子直接表达"想玩但不知道玩什么/选不出来"，其中 E2（2026-02）、E1（2026-04）明确给出时间成本量化："we burn 30+ minutes talking about what to play"、"we spend 15-30 minutes just figuring out what to play"。E7（113 赞 / 158 评论）是同类诉求中互动量最高的一条。
- **【事实】** 同一小问题至少催生 4 个独立自制工具（coopswipe、steamr.io、co-op.now、SquadRoll），E9 报告"400+ 人创建过 session"——说明有人不只是抱怨，还愿意动手用工具解决。
- **【事实】** 中文侧同样成立：贴吧/知乎存在大量以"人数+类型"为标题的求推荐帖（C8–C12）；B 站单条"双人/多人游戏推荐"播放 293.7 万、"双人联机推荐"191 万，说明这是有稳定大盘的内容品类（播放量是需求规模的代用指标）。
- **【事实】** "试玩踩雷/Demo"有独立内容供给：C5 标题直接写"多人+联机+免费=无脑买买买??小心这些会踩雷的独立游戏"，C6"试玩结束了，好多BUG"。
- **【推断】** 痛点真实且频次高（每周/每次开黑都会发生一次"选游戏"环节，而非一次性问题）；但它是**"高频低强度·摩擦型"痛点**——用户不会为它专门付费，痛点价值主要落在"省 30 分钟"和"避免买错"，因此更适合做成免费/轻量工具而非付费订阅。

#### 频率

- **【事实】** 时间锚点集中在"周末/假期/下班后的游戏夜"：E2"a couple real game nights a week"、E3"Lan-Partys on a regular basis"、C14/C15 围绕假期与异地。
- **【推断】** 触发频率 ≈ 每周 1–2 次/小队；单次成本 15–40 分钟（E1/E2/E4 三处独立量化）。一年下来是"十几小时/队"的纯浪费，量级足够支撑一个自动化工具的存在。

#### 现有解决方案为什么不够（基于上面证据）

1. **【事实】Steam 商店/标签页只解决"有哪些"，不解决"今晚我们这群人玩哪个"**：C8 的标题就是一个完整的多人条件串（4 人 + 沙盒/开放世界 + PVE/PVP 均衡），商店没有这样的入口；E8 作者也把"是否跑得动某人的电脑""要不要更多人"列为现存工具没覆盖的点。
2. **【事实】"拥有关系"没被建模**：E1（someone doesn't own the game）、E8（"有时需要更多人"）、E9（"每天在 discord 里争"）指向同一件事——推荐必须知道"这群人各自库里有什么"，C2 的 336 赞评论进一步说明"是否双方都要买"会直接决定能不能玩。
3. **【事实】口碑信息是"UP 主标题/评论区"形态，不可结构化查询**：C5/C6 这类"踩雷警示"内容存在，但用户要自己刷；B 站评论里甚至出现"省流:没有手机游戏适合情侣联机"（C4，1623 赞）这种对"推荐内容本身不信任"的表达。
4. **【推断】现有竞品（含 Reddit 帖里自建的工具）多停在"投票/转盘/共同拥有列表"，缺少"按九档口碑 + 理由 + 避雷"的分层输出**——这正好是「按 Steam 评价九档分组 + 真实评测提炼理由 + 避雷榜」可以补的位。

#### 对产品设计的三条直接约束（从证据反推）

1. 追问必须收敛到 **人数（2/3/4+）、是否同屏/异地、预算（免费/愿付区间）、恐怖接受度** 四项（对应第 1/2/4/5 维度）；这四项在证据中反复出现，其余可作为可选。
2. 输出必须带 **"为什么"（评测提炼）+ "别买"（避雷）**，否则和 B 站/知乎的推荐帖无差异（C4 的"省流"评论就是反例）。
3. 需要显式处理 **"朋友是否已拥有/是否要各买一份"**，这是本次证据里最锋利、也最少被现有工具处理的一条（C2、E1、E8、E9）。

---

### 附：本次未完成/受限项（供 lead 决策是否补调研）

- r/Steam、r/patientgamers、r/mcp 定量样本：pullpush 429 限流，未取到（"未找到"）。
- 知乎/贴吧正文：403，仅有标题；若需原文摘录，需要可用的代理或登录态。
- NGA：`site:nga.cn` 只返回首页，未命中具体楼（"未找到"）。
- 小红书：需登录，仅拿到 1 条标题（与主题相关性弱）。
- B 站：已拿到 10 条评论原文（C1–C4 引用其中 4 条高赞）；弹幕/更多页评论因 412 限流未扩采。

---

## 3. 数据源与技术可行性（实测）

> 本节全部结论来自 **2026-10-28 在本机（中国大陆网络、经本地代理）实测**，取证文件在 `research/raw/`。标注约定：**【实测】**= 本次真实请求得到的结果；**【文档】**= 官方/社区文档记载；**【推断】**= 基于前两者的判断，未验证。

### 3.1 先解决"连不上"：主机 × 端点实测矩阵

这台机器上 **Steam 官方域名全线不可用**（0.27–0.4 秒内 `Connection was reset`，DNS 解析正常 → **TLS/SNI 层阻断**，换 IP/加 Host 头都无效），但 **Akamai CDN 上的商店主机可用**。**【实测】阻断是间歇的**：同一天 09:47 之前 `store.steampowered.com` 曾 200 成功，09:54 起 36/36 全失败 → 代码里**不能假设"永远通"或"永远不通"**，必须可配置 + 自动探测。

> **【v1.1】** 上述"全线不可用"是**未走代理**时的结果。用户本机设置了 `HTTPS_PROXY=http://127.0.0.1:7890`，经代理实测 `store.steampowered.com/api/appdetails` 与 `api.steampowered.com` 均返回 200，只有 `appreviews` 失败（IncompleteRead）。最可能原因：Clash 规则把 `steampowered.com` 设为 DIRECT。修正方法见技术路径 Phase 0。

| 主机 | appdetails | `/app/<id>` 商店页 | appreviews | tagdata | search/results | 结论 |
|---|---|---|---|---|---|---|
| `store.steampowered.com` | ✗ RST | ✗ RST | ✗ RST/EOF | ✗ | ✗ | 域名被阻断 |
| `store.akamai.steamstatic.com` / `cdn.akamai.steamstatic.com` | ✗ | ✗ | ✗ | ✗ | ✗ | 被阻断 |
| **`steamstore-a.akamaihd.net`** | **✓ 200** | **✓ 200（307KB HTML）** | **✗ RST**（POST 返回 403） | **✓ 200** | **✓ 200** | **可用主力源** |
| `api.steampowered.com`（GetAppList v2 等） | ✗ RST | — | — | — | — | 被阻断 |
| `steamcommunity.com` / `steamcommunity-a.akamaihd.net` | ✗ RST | ✗ | ✗ | ✗ | ✗ | 被阻断 |
| `steamwebapi.akamaihd.net` | NXDOMAIN | — | — | — | — | 不存在 |
| `api.steamcmd.net/v1/info/<appid>` | ✓ 200 | — | — | — | — | 第三方 SteamCMD appinfo 镜像，可作补充 |
| `www.pcgamingwiki.com/w/api.php` | ✓ 200（HTML 页面 403） | — | — | — | — | MediaWiki API 可读，见 3.6 |
| `steamspy.com` / `steamdb.info` / `co-optimus.com` | ✗ 403 / RST / 403 | — | — | — | — | 不可用 |

**【实测】可用的完整请求样例**

```bash
BASE=https://steamstore-a.akamaihd.net
# 1) 游戏详情
curl "$BASE/api/appdetails?appids=548430&cc=cn&l=schinese"   # v1.1：原为 l=zh，Steam 不识别
# 2) 商店页（可解析评测/标签/相似游戏）
curl "$BASE/app/548430/?l=schinese&cc=cn"
# 3) 全站热门标签（含中文名）
curl "$BASE/tagdata/populartags/schinese"
# 4) 搜索（支持类别/标签/排序）
curl "$BASE/search/results/?query&start=0&count=50&category1=998&category3=9&tags=1685&sort_by=Reviews_DESC&infinite=1&cc=cn&l=schinese"
# 5) 名称 → appid（JSON API，体积小，适合"用户报游戏名"场景）
curl "$BASE/api/storesearch/?term=%E6%81%90%E9%AC%BC%E7%97%87&l=schinese&cc=cn"
# 6) 官方类别表（73 条，见 3.2）
curl "$BASE/actions/ajaxgetstorecategories"
```

**【推断】部署建议**：把商店主机做成可配置项，默认 `store.steampowered.com`，探测失败自动 fallback 到 `steamstore-a.akamaihd.net`；**不要**把 CDN 主机写死（它是 Valve 的内部主机，随时可能变）。这同时解决"海外用户/国内用户"两套网络环境。

### 3.2 `appdetails` 能拿到什么

**【实测】** 以《深岩银河》548430（原测试用 `cc=cn&l=zh`，**v1.1：应改为 `l=schinese`**）为例，返回 JSON 顶层 key 共 35 个：

`type, name, steam_appid, required_age, is_free, controller_support, dlc, detailed_description, about_the_game, short_description, supported_languages, reviews, header_image, capsule_image, capsule_imagev5, website, pc_requirements, mac_requirements, linux_requirements, legal_notice, developers, publishers, price_overview, packages, package_groups, platforms, metacritic, categories, genres, screenshots, movies, recommendations, achievements, release_date, support_info, background, background_raw, content_descriptors, ratings`

对"今晚玩什么"真正有用的字段：

| 字段 | 实测值（548430） | 用途 | 可靠性 |
|---|---|---|---|
| `name` / `steam_appid` | 深岩银河 / 548430 | 展示、跳转 | 高 |
| `type` | `game`（Demo 为 `demo`） | 区分 Demo | 高 |
| `is_free` | `false` | 免费游戏判定（CS2/永劫无间为 true 且无 price_overview） | 高 |
| `price_overview` | `{"currency":"CNY","initial":10800,"final":3240,"discount_percent":70,"initial_formatted":"¥ 108.00","final_formatted":"¥ 32.40"}` | 预算/折扣筛选 | 高（免费游戏该字段缺失） |
| `categories[]` | `[{id:2,description:"单人"},{id:1,"多人"},{id:9,"合作"},{id:38,"在线合作"},…]` | **联机形态判定**，见下表 | 高 |
| `genres[]` | `[{id:1,description:"动作"}]` | 类型（很粗，不足以判断恐怖） | 中 |
| `recommendations.total` | `304172` | 评测总量（判断"评测太少不可靠"） | 高 |
| `release_date` | `{"coming_soon":false,"date":"13 May, 2020"}` | 新品/年限筛选 | 高 |
| `platforms` | `{"windows":true,"mac":false,"linux":false}` | 平台 | 高 |
| `supported_languages` | HTML 片段 | 中文支持判断（需自己解析） | 中 |
| `short_description` / `detailed_description` | 中文文案 | 给 LLM 做初筛/生成理由 | 高 |
| `demos` | `{"appid":3101730,"description":""}` | **Demo 是否可用** | 中（多数游戏无此字段，见 3.3） |
| `metacritic` | 可缺省 | 媒体分（国内用户参考价值低） | 低 |
| `dlc` / `packages` / `screenshots` / `movies` | — | 本项目不用 | — |

**【实测/Bug 提醒】三个会坑死人的细节**：
- `reviews` 字段**不是好评率**，而是媒体引语 HTML（`"…"<br>5/5 – Giant Bomb`）；好评率只能从 3.4 的替身通路拿。
- **【v1.1】Steam 的语言参数应为 `l=schinese`，`zh` 不是 Steam 认可的语言代码，这大概率就是下面 12/16 返回英文的原因（待实测）。** 原结论：`l=zh` **不保证**中文：16 个样本里 **12 个 categories 的 description 仍是英文**、4 个中文 → **必须自建 `id → 中文名` 映射表**（官方表 73 条，见下），不要把 desc 直接展示给用户。
- 一次请求多个 appid 时必须带 `filters=price_overview`，否则该 appid 返回 `null` / HTTP 400，且**多个 filter 不能组合**→ 要完整详情只能**逐个 appid 拉**（配合 200 次/5 分钟的限流，必须缓存）。

**【实测】用 `categories` 判断联机形态——这是本项目最关键的字段**

先看官方类别表（`actions/ajaxgetstorecategories`，**73 条**，`show_in_search=1` 表示可在搜索里筛）：

| id | 官方英文名 | 商店中文名 | 检索用途 |
|---|---|---|---|
| 2 | Single-player | 单人 | 排除项 |
| 1 | Multi-player | 多人 | 基础判定 |
| 9 | Co-op | 合作 | 联机合作（泛） |
| 38 | Online Co-op | 在线合作 | **"和朋友联机"主判定** |
| 39 | Shared/Split Screen Co-op | 同屏/分屏合作 | 本地沙发合作 |
| 24 | Shared/Split Screen | 同屏/分屏 | 本地多人 |
| 36 | Online PvP | 在线 PvP | 对战类 |
| 37 | Shared/Split Screen PvP | 同屏/分屏 PvP | 本地对战 |
| 49 | PvP | 玩家对战 | 对战类（泛） |
| 47 / 48 | LAN PvP / LAN Co-op | 局域网 PvP / 局域网合作 | 局域网联机 |
| 27 | Cross-Platform Multiplayer | 跨平台多人 | 跨端 |
| 20 | MMO | 大型多人在线 | MMO |
| 10 | Game Demo | 游戏 Demo | **Demo 池筛选**（`show_in_search=0` 但搜索可用） |
| 44 | Remote Play Together | 远程同乐 | 单人游戏也能"远程同乐"当联机玩 |

**【实测】** 548430 的 categories 实际返回：`单人(2)、多人(1)、合作(9)、在线合作(38)、Steam 成就(22)、完全支持控制器(28)、Steam 交易卡(29)、字幕(13)`（另有若干无障碍类）。→ 判定逻辑：`38 in ids` 为"在线合作"，`39 或 24 in ids` 为"本地合作"，`36/49/37 in ids` 为"PvP"。**没有**"支持几人"字段。

**【文档】批量与限制**：社区维护的 Steam 内部 API 文档（Revadike/InternalSteamWebAPI）记载 `appdetails` 现在 **限流 200 次/5 分钟**，且**一次请求多个 appid 已不再返回完整数据**，必须带 `filters=` 只取所需字段。→ 实践中：`filters=basic,categories,price_overview,release_date,recommendations`，一次只查 1 个 appid，其余走队列 + 缓存。

### 3.3 Demo / 价格 / 免费游戏的字段真相

**【实测】Demo 的真实结构**（《Game of Fate》2963550 ↔ Demo 3101730）：

| 对象 | `type` | `demos` | `fullgame` | `price_overview` |
|---|---|---|---|---|
| 正式游戏 2963550 | `game` | `{"appid":3101730,"description":""}` | — | `initial 4500 / final 2250 / -50%` |
| Demo 3101730 | **`demo`** | — | `{"appid":"2963550","name":"Game of Fate: Chasing Through Time"}` | **无** |

结论：
- `demos` 字段**只给 appid，不给描述**，且**很多游戏根本没有该字段**（本次抽样 16 款里仅少数有）→ 不能拿它做"有 Demo"的唯一依据。
- 更可靠的两条路：① 搜索 `category1=10`（Game Demo 类别）拿 Demo 池，**【实测】`total_count=41854`**；② 用 Demo 的 `fullgame` 反查正式版（反过来也可：正式版的 Demo 常是独立 appid，名称含 "Demo"/"试玩"）。

**【实测】`cc=cn` 的价格字段就是人民币**：`currency: CNY`、`initial/final` 是**分**（10800 = ¥108）、`discount_percent` 直接可用、`*_formatted` 是展示串。免费游戏（730/892970 等）**没有 `price_overview`**，只有 `is_free: true`；付费 Demo 也可能有 `price_overview`（少见）。

### 3.4 `appreviews`：字段、九档映射与"为什么连不上"

**【文档】**（Revadike/InternalSteamWebAPI，`Get-App-Reviews`）关键能力：

| 参数 | 取值 | 说明 |
|---|---|---|
| `filter` | `recent` / `updated` / `all` / `summary` | `all` = **按有用度排序**（配 `day_range` 滑动窗口） |
| `language` | `schinese`、`english`、逗号分隔多语言 | **按语言筛评测**（"中文评测"就靠它） |
| `day_range` | 1–365 | 仅 `filter=all` 时生效 |
| `review_type` | `all` / `positive` / `negative` | 好评/差评 |
| `purchase_type` | `all` / `steam` / `non_steam_purchase` | 是否 Steam 购买（排除 key 用户） |
| `playtime_filter_min/max` | 小时 | **排除"没玩多久就测评"的水评** |
| `num_per_page` | ≤100（默认 20） | 页大小 |
| `filter_offtopic_activity` | 0/1 | 默认剔除"差评轰炸"，=0 可保留 |
| `use_review_quality` | 0/1 | 默认剔除"无信息量"评测（新有用性系统） |
| `cursor` | `*` 起步 | 翻页 |

`query_summary` 里**没有**中文，只有枚举 + 英文描述：`review_score`（1–9）、`review_score_desc`（`Very Positive` 等）、`num_reviews`、`total_positive`、`total_negative`、`total_reviews`。**中文"好评如潮"要自己映射**（这就是九档表的价值）。

**【实测 + 文档】九档 ↔ `review_score` 对应表**（本次实测样本见右列）

| review_score | 英文 desc | 中文文案 | 星级建议 | 本次实测样本 |
|---|---|---|---|---|
| 9 | Overwhelmingly Positive | 好评如潮 | ★★★★★（5） | 泰拉瑞亚 97.54%、星露谷 98.53%、双人成行 95.67%、哈迪斯 97.99%、深岩银河 97.3% |
| 8 | Very Positive | 特别好评 | ★★★★½（4.5） | 艾尔登法环 93.17%、恐鬼症 94.57%、英灵神殿 93.64%、PAYDAY 2 89.88% |
| 7 | Positive | 好评 | ★★★★（4） | 本次未采到样本（见备注） |
| 6 | Mostly Positive | 多半好评 | ★★★½（3.5） | 战争雷霆 71.22%、永劫无间 73.33% |
| 5 | Mixed | 褒贬不一 | ★★★（3） | PUBG 60.54% |
| 4 | Mostly Negative | 多半差评 | ★★½（2.5） | 未采到样本 |
| 3 | Negative | 差评 | ★★（2） | 未采到样本 |
| 2 | Very Negative | 特别差评 | ★½（1.5） | 未采到样本 |
| 1 | Overwhelmingly Negative | 差评如潮 | ★（1） | 未采到样本 |

- **【文档】** 官方 App Reviews 文档的示例里，TF2（440）`review_score=8`、`review_score_desc="Very Positive"`、672753/747091≈90.05% → 与上表一致。
- **【实测】** 商店搜索结果卡片的中文 tooltip 给的是"档位文案 + 百分比"，例如"好评如潮，此游戏的 1,809 篇用户评测中有 96% 为好评"、"特别好评，…12,173 篇…91%…" → **96% 归好评如潮、91–94% 归特别好评**，佐证 95% 是 9 档分界。
- **【推断·v1.1 补全】** 社区口径的阈值同时看"好评率 + 评测数"：好评如潮 = 95%+ 且 ≥500 篇；特别好评 = 80%+ 且 50–499 篇，或 80–94% 且 ≥500 篇；好评 = 80%+ 且 10–49 篇；多半好评 = 70–79%；褒贬不一 = 40–69%；多半差评 = 20–39%；差评 = 0–19% 且 10–49 篇；特别差评 = 0–19% 且 50–499 篇；差评如潮 = 0–19% 且 ≥500 篇。Valve **没有公开成文阈值表**，7 档"好评"通常是"80–94% 但评测数较少"时出现，故本报告不写死阈值，**直接用 `review_score` 枚举**（最稳）。
- **【推断，重要】** 用户语言筛选会改变档位：同一款游戏 `summaryDisplayed`（全语言）与 `summaryYourLanguage`（简中）可能差 1–2 档（实测英灵神殿：全语言 93.64%/特别好评，简中 83.37%）。→ 榜单要**注明是"全语言口碑"还是"简中口碑"**，否则用户会觉得"被骗"。

**【实测】`appreviews` 在本机到底怎么失败（关键更正：不是"空响应"，是 **302 重定向拒绝**）**

| 路径 | 实测结果（2026-10-28 09:55–09:57） |
|---|---|
| `store.steampowered.com/appreviews/413150?json=1…` | **HTTP 000**，TLS 握手即 RST（12 轮探测一致） |
| **`steamstore-a.akamaihd.net/appreviews/413150?…`** | **HTTP 302** + `Content-Length: 0`（**0 字节正文，无 JSON**）+ `Location: https://store.steampowered.com/` |
| 同上加 `-L` 跟随重定向 | 跳进**已被阻断**的 `store.steampowered.com` → RST/EOF |
| 同路径 POST | **403** |

6 种绕法**全部失败**（间隔 2s 逐条测）：① 原样 GET ② `-L` ③ `--http1.1` + `Referer`/`Origin`/`Accept: application/json` ④ 伪造 `Host: store.steampowered.com`（SNI 仍是 akamaihd）⑤ 全套 Cookie（`steamCountry`/`Steam_Language`/`birthtime`/`lastagecheckage`/`mature_content`/`sessionid`）⑥ 换参（`filter=summary`/`l=schinese`/`cursor=*`/换 appid 730）。

> **这正好解释了你在本地代理下遇到的 `IncompleteRead`**：请求库默认跟随 302 → 落到被墙域名 → 连接被切断时已声明要读的 body 读不满，`http.client` 就抛 `IncompleteRead(0 bytes read)`；不跟随重定向则拿到 0 字节空 body。**同一个 CDN 主机上，`/api/appdetails`、`/app/<id>`、`/search/results`、`/tagdata/*` 当时全部 200**——所以不是"整个域名挂了"，而是 **`/appreviews` 这条路径被单独重定向走**。结论：**换域名救不了评测接口**，只能换出口或换数据来源。

> **【v1.1】** "换出口"最省钱的做法是 **GitHub Actions（海外机房，公开仓库免费）**：在 Actions 里抓 `appreviews`，产出 SQLite 快照分发给国内用户。本地开发时可先尝试把 Steam 域名改为走代理。

**【实测】三条不依赖 `appreviews` 的替身（都能拿到"中文九档 + 百分比 + 评测数"）**

1. **搜索卡片 tooltip（最省事，一次请求 100 款）**：`search/results` 每条结果里都有
   `data-tooltip-html="褒贬不一<br>此游戏的 97,017 篇用户评测中有 62% 为好评。"`
   实测样本：`570 Dota2 78% → 多半好评`、`381210 黎明杀机 62% → 褒贬不一`、`4356430 34% → 多半差评`、`2338770 16% → 差评如潮`。→ **候选池列表本身就带档位，不必为每款游戏再请求 appdetails**（省下 200/5min 的配额）。
2. **商店页**：`/app/<id>/?l=schinese&cc=cn` 里的 `data-featuretarget="appreviews"` 的 `data-props`（HTML 转义，需 `html.unescape`）→ `summaryDisplayed`/`summaryYourLanguage` + `filter_options.rgLanguageOutliers[]`（**逐语言** 好评率，含 `schinese`）。
3. `appdetails.recommendations.total` 只有总数无比例，只能判断"评测够不够多"，**不能定档**。

**【实测】可行的绕过/降级路线**（按推荐度）：
1. **降级到商店页解析**（已验证 100% 可行）：`/app/<id>/?l=schinese&cc=cn` 页面里 `data-featuretarget="appreviews"` 的 `data-props`（HTML 转义）含 `summaryDisplayed`、`summaryYourLanguage`、`nReviewsPositive/Negative`、`eReviewScore(1-9)`、`nAgeInDays`，以及 `filter_options.rgLanguageOutliers[]`（**逐语言** `{nReviews, nReviewsPositive, eReviewScore, strLanguage}`，含 `schinese`）。→ 不调 appreviews 也能拿到"九档 + 简中好评率"。
2. **同页还能拿到**：`InitAppTagModal(<appid>, [{"tagid":…,"name":"恐怖","count":…}])`（该游戏的标签 + 中文名 + 票数，**判断恐怖不需要额外接口**）、`data-ds-appid`（"更多类似产品" appID，天然相似游戏池）、鉴赏家数量。
3. **"不跟随 302"没有意义**（拿到的是 0 字节）；正解是**换出口**：海外 VPS / 可配置代理 / GitHub Actions 定时任务（见第 4–5 条）。
4. **部署到海外 VPS / Cloudflare Worker 做代理**（最省心，成本 ~$0–5/月），MCP Server 本地只做缓存与编排。
5. **离线预抓**：榜单数据本身就是"每天变一次"的量级，用 GitHub Actions 定时抓全量候选池（几千款游戏的 categories + 商店页评测摘要），打包成 JSON 随 MCP Server 分发。→ **这是本项目性价比最高的架构**（见 3.9）。

### 3.5 恐怖标签与标签体系

**【实测】** 标签有两条可用路径：

1. **全站热门标签表**：`https://steamstore-a.akamaihd.net/tagdata/populartags/schinese`（英文版 `/english`）返回 **429 个热门标签**的 `tagid + 中文名 + 英文名`。**建一次本地映射表即可**，不需要运行时请求。
2. **每款游戏的标签**：搜索结果 HTML 的 `data-ds-tagids="[1667,3843,3839,3978,6730,…]"`，或商店页 `InitAppTagModal`（带中文名与票数）。→ **不需要 SteamSpy**（本机 403）。

**【实测】关键 tagid**（来自 tagdata + 搜索页 tagids 交叉验证）：

| tagid | 标签 | 用途 |
|---|---|---|
| **1667** | Horror | 恐怖（总类） |
| 1721 | Psychological Horror | 心理恐怖（吓人程度更高） |
| 3978 | Survival Horror | 生存恐怖 |
| 1685 | Co-op | 合作 |
| 3843 | Online Co-Op | 在线合作（**核心筛选**） |
| 3841 | Local Co-Op | 本地合作 |
| 3859 | Multiplayer | 多人 |
| 7368 | Local Multiplayer | 本地多人 |
| 1775 | PvP | 玩家对战 |
| 6730 | PvE | 玩家对环境 |
| 4508 | Co-op Campaign | 合作战役（**有剧情/能玩多久**） |
| 4840 | 4 Player Local | 4 人本地 |
| 7178 | Party Game | 派对游戏 |
| 128 | Massively Multiplayer | 大型多人在线 |
| 1662 | Survival | 生存 |

**【实测】** 交叉验证例：`DEATH IN UNISON` 的 tagids = `[1667,3843,3839,3978,6730,…]`（恐怖 + 在线合作 + 生存恐怖）；`Levelhead` = `[1625,1685,8122,7178,3841,597,3859]`（合作 + 派对 + 本地合作）。→ **"恐怖 × 在线合作"可以用 tagid 交集直接算**：`1667/1721/3978 ∩ 3843/1685`。

**恐怖判定建议**：`1667 ∈ tags` 为主，`1721/3978` 为"重度恐怖"，再用 `tags=1667` 反向拉候选池；不要用 `genres`（只有"动作/独立"这种粗分类）。

**【实测】候选池怎么拉：`search/results` 的真实数字与 4 个坑**

```powershell
https://steamstore-a.akamaihd.net/search/results/?query&start=0&count=100&infinite=1&cc=cn&l=schinese&category1=998&tags=3843&sort_by=Reviews_DESC
```

| 查询（`category1=998` = 游戏类） | `total_count`【实测】 |
|---|---|
| `tags=3843`（在线合作）+ 按评测数排序 | **4,073** |
| `tags=1685`（合作，含本地） | **5,735** |
| `tags=1667`（恐怖，带 `cc=cn&l=schinese`） | **10,008** |
| `tags=1667`（不带 `cc/l`） | 23,450（**差异未解释，疑区域过滤**） |
| 无 tags、仅按评测数排序 | 71,538 |

- 每条结果（HTML 片段需解析）里就有：`data-ds-appid`、`data-ds-tagids="[1667,3843,…]"`、`<span class="title">`、`data-price-final`（分）、`platform_img win/mac/linux`、**评测 tooltip（档位+百分比+总评测数）**。
- ⚠️ **`count=20` 实测返回 25 条**，`count=100` 返回 100 条 → 以实际返回条数为准，不要用 count 做分页步长。
- ⚠️ **`query=<中文关键词>` 不生效**（实测返回全量 186,134 条）→ 名称检索必须用 `api/storesearch/?term=phasmophobia&l=schinese&cc=cn`（实测 200、440 字节，直接给结构化 `id/name/CNY 价格`）。
- ⚠️ 搜索是最容易触发风控的接口（本次 20+ 次请求后开始 RST）→ 全局串行 + ≥2s 间隔 + 缓存。
- ⚠️ **【v1.1】** `data-ds-tagids` 可能只包含该游戏的**前若干个**标签（未验证是否完整）。判断"是否恐怖"不要只依赖它：建议额外跑一次 `tags=1667`（以及 1721/3978）× 联机条件的搜索，把命中的 appid 集合标记为恐怖。

### 3.6 "支持几人联机"：没有结构化字段，但有替代方案

**【实测】`appdetails` 里没有任何"最多几人"字段**（categories 只表达"支持在线合作"，不表达 2/4/8 人）。可选替代：

| 方案 | 可行性 | 实测/说明 |
|---|---|---|
| **PCGamingWiki MediaWiki API** | ✅ 最佳 | `https://www.pcgamingwiki.com/w/api.php?action=parse&page=Deep_Rock_Galactic&prop=wikitext&format=json&formatversion=2` 返回 26KB wikitext，内含模板：`{{Network/Multiplayer \| local play = hackable \| local play players = 4 \| local play modes = co-op \| online play = true \| online play players = 4 \| online play modes = co-op }}` → **有结构化的 `online play players = 4`**。注意：HTML 页面被 Cloudflare 403，**只能走 API** |
| 商店页/详情页文本 | ⚠️ 部分 | 中文商店页有"在线合作/同屏合作"标签，人数常写在描述里（需 LLM 抽取） |
| Steam 鉴赏家/攻略帖 | ⚠️ 噪音大 | 不可结构化 |
| SteamSpy / SteamDB | ✗ 本机不可用 | 403/RST；且它们也没有官方人数字段 |
| 人工维护小表 | ✅ 兜底 | 联机游戏池有限（几千款），Top 500 手工/半自动标注成本可接受 |

**【推断】** 覆盖度：PCGW 对**有 wiki 页**的游戏（多为知名游戏）覆盖好，对**新 Demo 覆盖差**；因此"人数"应作为**可选字段**，缺失时降级为"在线合作（人数未标注）"，不要因此排除游戏。

### 3.7 频率限制、API Key 与 ToS 风险

| 接口 | 限流 | 需 Key | 条款风险 |
|---|---|---|---|
| `appdetails` | **【文档】200 次/5 分钟** | 否 | 未公开文档的商店接口；Valve 可随时改；商用/大规模抓取违反 Steam 订阅协议精神 |
| `appreviews` | **【文档】"No known rate limit"**（实际建议 ≤1 req/s、一页 100 条） | 否 | 同上 |
| `search/results`、`tagdata` | 未见公开限制 | 否 | 同上，**搜索最容易触发风控**（本次 20+ 次请求后被 RST） |
| `api.steampowered.com`（官方 Web API） | 【文档】每日 10 万次（需注册） | **是** | 有正式条款，最安全，但只提供玩家/成就/好友类数据，**没有商店与评测** |
| PCGamingWiki API | 无硬限制 | 否 | CC BY-NC-SA，需署名、非商用 |

**【推断】合规建议**：① 只读、不登录、不碰玩家隐私；② UA 标明项目与联系方式；③ 全量数据走"离线预抓 + 本地缓存 + 每日增量"，在线只查缓存未命中项；④ 对外发布时 README 写明数据来源与"非官方接口"免责声明；⑤ 不做价格预测/交易类功能。

### 3.8 国内网络：为什么 `appdetails` 通、`appreviews` 空

见 3.4 的四个原因。补充三条**工程化解法**：

1. **请求层**：`Accept-Encoding: identity`（禁 gzip，避免中间盒半包）、`Connection: close`（避免 keep-alive 复用坏连接）、固定 `User-Agent`；失败重试 3 次（1s/3s/9s）。
2. **传输层**：优先**直连**测速，再决定是否走代理；代理规则里把 `steamstore-a.akamaihd.net` 设为直连或固定节点（避免节点切换导致半包）。
3. **数据层**：**永远不要**让 MCP 工具在用户对话中实时抓 appreviews（慢且不稳）；榜单数据用每日定时任务预生成（3.9），工具只读本地 SQLite/JSON。

### 3.9 缓存策略（推荐直接照做）

| 数据 | 变化频率 | TTL | 存储 |
|---|---|---|---|
| 游戏基础信息（name/categories/genres/platforms/开发商） | 极低 | 30 天 | SQLite |
| 价格 / 折扣 | 高（每日） | 1–6 小时 | SQLite |
| 评测摘要（九档 + 简中好评率 + 评测总数） | 中（每日） | 6–24 小时 | SQLite |
| 单条评测正文（引用用） | 低 | 7 天 | SQLite + 原文链接 |
| 热门标签表（tagdata） | 极低 | 7 天 | JSON 资源 |
| 候选池（搜索 total_count / appid 列表） | 中 | 24 小时 | SQLite |
| PCGW 人数 | 极低 | 30 天 | SQLite |
| 商店页解析结果 | 中 | 6 小时 | SQLite |

**架构建议（【推断】，但基于实测约束）**：`数据管道（离线，GitHub Actions 每日/每周）` → `data/games.sqlite`（随仓库/Release 分发）→ `MCP Server（纯本地读，毫秒级）`。这样：**不需要用户配 Key、不依赖国内网络、不触发限流、响应快**，只有"用户点名某款游戏"时才走一次实时请求（带 fallback）。这也是对抗"Steam 接口随时挂/随时变"的唯一稳妥设计。

---

## 4. 推荐理由怎么生成（"凭什么信你"）

这一节是产品与"又一个榜单网站"的分水岭：**理由必须是可溯源的玩家原话 + 结构化维度，而不是模型编的一段安利。**

### 4.1 从评测到"联机体验卡"的流水线

```
候选池（3.5/3.9）
  → 取评测：appreviews?json=1&filter=all&language=schinese&day_range=90
              &playtime_filter_min=2 &num_per_page=50
              # 参数名取自 Revadike 社区文档；本机通路不可用（§3.4），参数未实测
  → 句子级切分 + 关键词打标（中文）
  → 抽证据句（保留 votes_up、原文链接、时间）
  → LLM 只做"归纳"，不做"创作"：填固定 JSON schema
  → 校验：quote 必须能在原文里精确匹配到（防幻觉），每款 ≥2 条证据
```

**【文档依据】**（Revadike 社区整理的 InternalSteamWebAPI wiki，2026-10 抓取）`appreviews` 支持 `filter=all`（按"最有帮助"排序）、`language=schinese`（可逗号分隔多语言）、`playtime_filter_min`（排除"玩 0.2 小时就测评"）、`review_type=positive/negative`、`purchase_type`、`filter_offtopic_activity`、`cursor` 分页——**这套参数就是"筛选高质量中文好评/差评"的现成工具**，不需要自己写情感分析模型（有 ML 背景也不建议第一版就上，成本/收益不划算）。⚠️ 本机 `appreviews` 走不通（§3.4），这些参数名**未实测**，上线前需在可用网络验证。

**关键词表（中文，实测语料里高频）**

| 维度 | 关键词（正则片段） | 产出字段 |
|---|---|---|
| 联机质量 | 联机\|开黑\|组队\|队友\|匹配\|人满\|房间\|服务器 | `coop_quality` |
| 网络 | 延迟\|卡顿\|掉线\|加速器\|网络\|ping\|断连 | `network` |
| 时长 | 几小时\|小时\|通宵\|一晚上\|耐玩\|重复\|刷 | `session_length` |
| 门槛 | 上手\|教程\|难\|硬核\|新手\|简单 | `difficulty` |
| 恐怖 | 吓人\|恐怖\|jump scare\|不敢\|害怕 | `horror_level` |
| 内容量 | 剧情\|关卡\|内容\|更新\|DLC | `content` |
| 雷点 | 退款\|垃圾\|不建议\|别买\|外挂\|平衡 | `dealbreaker` |

**输出 schema（每款游戏一张卡）**

```json
{
  "appid": 548430,
  "name": "深岩银河",
  "review": {"score": 9, "tier": "好评如潮", "pct": 97.3, "total": 304172,
             "lang_note": "全语言口碑；简中好评率 93.2%"},
  "coop": {"online_coop": true, "players": 4, "players_source": "pcgamingwiki"},
  "why": "四人挖矿射击，职业互补，单局 20–40 分钟，适合一晚上刷几把。",
  "quotes": [
    {"text": "……（玩家原话，≤60 字）", "votes_up": 128, "lang": "schinese",
     "url": "https://steamcommunity.com/profiles/…/recommended/548430/"}
  ],
  "watch_out": "后期重复感明显；四人以下体验下降。",
  "evidence_count": 7
}
```

### 4.2 每条理由的三条硬规则（防止"看起来像真的"）

1. **引用必须可点开**：`quotes[].url` 指向 Steam 评测原文（`steamcommunity.com/.../recommended/<appid>/`），并且 `text` 是原文子串——上线前用脚本做一次**子串校验**，不通过就丢弃，不许改写。
2. **区分"全语言口碑"与"简中口碑"**：两者可能差 1–2 档（实测英灵神殿：全语言 93.64% / 简中 83.37%）。卡片上必须写清用的是哪一个，否则用户点进商店看到"多半好评"会认为你在骗他。
3. **"卡不卡"不许猜**：延迟取决于用户所在地区/加速器，评测只能给"有没有人抱怨掉线"。文案固定为"有 N 条评测提到网络/掉线"，**不给"不卡"的结论**。

### 4.3 评测少的新游戏 / Demo 怎么不坑人

| 场景 | 判定 | 处理 |
|---|---|---|
| 评测数 ≥ 500 且 ≥ 1 条中文高质量评测 | 可信 | 进主榜 |
| 评测数 50–499 | 半可信 | 进主榜但**降权**（同档位内排后），标注"评测较少" |
| 评测数 < 50 或 **Demo** | 不可信 | 单独一档「**试玩友好 / 未知区**」，标注"评测不足，仅凭玩法标签推荐" |
| 有 Demo 且 Demo 有独立评测 | 额外信号 | Demo 的评测数/好评率作为"试玩值不值"的独立指标 |
| 2026 年后新游 | — | 加"新品"标记，不与老游戏混排 |

**【推断】其它弱信号（拿来兜底，不单独用）**：开发商/发行商历史口碑（同厂商其它作品的中位好评率）、`recommendations.total` 的增长速度（每周抓一次即可算）、PCGW 标注的联机人数、社区标签。

**【实测支撑】** Demo 池可以量化获取：搜索 `category1=10`（Game Demo）本次 `total_count=41854`，且 Demo 是独立 appid（`type="demo"` + `fullgame` 反查正式版）。→ "Steam 新品节 Demo 专题"这个迭代功能在数据上是**完全可行**的，不需要额外数据源。

### 4.4 避雷榜怎么生成（差异化最大的一环）

- **规则**：同档位内，若"中文差评里出现 ≥3 次同一 dealbreaker（外挂/掉线/内容少/氪金）"，则进避雷榜；或 `review_score ≤ 5` 但商店首页仍在推（营销与口碑背离）。
- **文案**：`避雷点（来自 12 条中文差评）：匹配要等 5 分钟、外挂多、单局太短`，同样附原文链接。
- **为什么没人做**：所有竞品（含 Sarg338、imho）只做正向推荐（见 §1）；负向榜单天然适合传播，且**直接命中你的原始痛点"一个个试错"**。

---

## 5. MCP 交互设计："先追问一轮"怎么做最稳

### 5.1 三种方案对比

| 方案 | 做法 | 优点 | 缺点 | 兼容性 |
|---|---|---|---|---|
| **① 可选参数 → 返回"待追问清单"** | tool 参数全设可选；缺关键参数时不猜，返回结构化 `need_more_info`（问题 + 选项 + 默认值），由模型转头问用户，下一轮再带上答案调用 | 所有客户端都能用；实现最简单；**与 2026-07-28 新规范方向一致**（见 5.2）；失败可降级（模型不追问就直接用默认值出结果） | 多一轮往返；不同客户端模型"会不会照做"有差异；参数校验要写成软校验 | **全兼容** |
| **② MCP elicitation** | 工具执行中 `ctx.elicit(message, Schema)` 直接弹表单收集答案 | 体验最好：一次工具调用内完成，客户端渲染表单，可枚举选项 | 需要客户端支持；**协议正在换代，写法会分叉**；非交互会话会被取消；客户端渲染样式不可控 | 部分支持（见 5.3） |
| **③ MCP Prompts** | 提供一个 `prompt`（如"今晚玩什么"），里面是完整的追问脚本 + 调用约定 | 用户可见、可复用；Claude Desktop/Cursor 有 slash 入口；能沉淀"问法" | **只能用户主动触发**，模型不会自动用；逻辑分散在 prompt 与 tool 两处 | 大多数客户端支持 prompts |

**推荐组合（工程结论）**：
> **① 做主干（永远可用）＋ ② 做增强（用 SDK 的 resolver 写法，而不是裸 `ctx.elicit`）＋ ③ 做一个 prompt 当入口。三者共用同一套"追问问题定义"。**

### 5.2 为什么"① 是主干"不是保守，而是顺应规范

**【文档】** MCP 的 elicitation 在 **2025-06-18** 版规范引入（两种模式：Form 表单 / URL 跳转）。**【文档·v1.1 待核实】** 而 **2026-07-28** 版规范**取消了"服务端→客户端反向请求"**，改成 **MRTR（multi-round-trip）**：工具直接返回 `InputRequiredResult{ input_requests, request_state }`，客户端把答案放进 `input_responses` **重调同一个工具**，服务器再返回正常结果。

**【文档·v1.1 待核实，实现时以本机安装的 SDK 版本文档为准】** 官方 Python SDK 现状：
- `ctx.elicit()` / `ctx.elicit_url()` **只在 2025-11-25 及更早的连接上可用**；在 2026-07-28 连接上会失败。
- 跨版本写法是 **resolver**：参数标注 `Annotated[T, Resolve(fn)]`，或让工具返回 `InputRequiredResult`；SDK 在老连接上"直接发问题"，在新连接上"把问题作为返回值带回"。

也就是说：**"返回问题、让客户端再问一遍"正是新规范钦定的模式**，方案 ① 天然兼容两代协议——这就是选它做主干的硬理由。

FastMCP（Python）里落地要点（【文档】+【推断】）：
- tool 函数 `async def`，参数写 `Annotated[..., Field(description=...)]`，缺参分支返回 `{"status":"need_more_info","questions":[...]}`；
- 想用 elicitation 就写 resolver（`Resolve(...)` + `ElicitationResult[T]`），**不要**在工具体里直接 `await ctx.elicit()`，否则新版协议下会抛错；
- 声明 `outputSchema`（structured output），让客户端能拿到稳定 JSON。

### 5.3 主流客户端支持矩阵（只写有证据的）

| 客户端 | Elicitation | 证据 | 对本项目的含义 |
|---|---|---|---|
| **Cursor** | ✅ 支持 | 官方文档功能表直接列出 "Elicitation — Server-initiated requests for additional information from users — Supported" | 可用方案 ② |
| **Claude Code** | ✅ 支持（交互式） | 官方文档描述 "a call waiting on an open elicitation dialog"，并说明 `/tasks` 打开时对话框会被隐藏、**非交互模式下会请求服务端取消需要输入的调用** | 可用 ②，但 CLI 管道/脚本场景会退化为 ① |
| **Claude Desktop** | ❓ 未找到 | 官方帮助中心文档中未检索到 elicitation 相关表述 | 按 ① 设计 |
| **Zed** | ⚠️ 推断未完成 | 官方文档写 "We welcome contributions that help advance Zed's MCP feature coverage (Discovery, Sampling, Elicitation, etc)" | 按 ① 设计 |
| **Cherry Studio / VS Code Copilot / Cline / Windsurf / Continue** | ❓ 未找到 | 各自官方文档中未检索到 elicitation 关键字的支持说明 | 按 ① 设计 |

> 【事实】上表"未找到"= 我抓取了对应官方文档全文并搜索 `elicitation` 无匹配，**不等于一定不支持**，只是没有可引用证据；产品上不能依赖它。

**结论**：如果只做 ②，你在 Claude Desktop（最大的用户群）上大概率直接不可用；而 ① 在所有客户端上都成立。**② 只作为"检测到老协议连接时的甜点"**。

### 5.4 榜单返回什么格式：结构化 JSON 为主，Markdown 为辅

**【文档】** MCP 已支持 **structured output**（`outputSchema` + `structuredContent`），工具可以返回机器可读 JSON，同时保留给 LLM 的文本内容。

| 方式 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| 纯 Markdown | 任何客户端都能显示；可直接复制进微信群 | 星级/分组/价格无法稳定复用；LLM 二次处理易丢字段 | 作为 `text` 摘要 |
| **结构化 JSON + 简短 Markdown** | 模型可自行渲染成表格/卡片；字段可被下游逻辑复用（排除已玩、对比价格）；九档分组天然是 JSON 结构 | 需要客户端支持或模型自己排版 | **推荐** |
| MCP Apps / 交互 UI | 最漂亮 | 生态太新，兼容性差 | 后续迭代 |

**建议返回结构**：
```json
{
  "status": "ok",
  "query": {"players": 4, "horror": "no", "budget": "free_or_demo", "session": "medium"},
  "groups": [
    {"tier": "overwhelmingly_positive", "label": "好评如潮", "stars": 5, "items": [...]},
    {"tier": "very_positive", "label": "特别好评", "stars": 4, "items": [...]}
  ],
  "avoid_list": [{"name": "...", "reason": "...", "quotes": [...]}],
  "meta": {"lang_note": "口碑口径=全语言；简中好评率另附", "cached_at": "2026-10-28T10:00:00+08:00",
           "candidates_scanned": 412, "disclaimer": "非官方数据，见 README"}
}
```
同时在 `content[0].text` 里放一份 **≤15 行的 Markdown**（"4 人 / 不恐怖 / 免费 Demo 优先" 的标题 + 每档 2–3 款 + 一句推荐理由 + 链接），这样即使模型不排版，用户也能直接用。

---

## 6. MVP 方案

### 6.1 第一版 4 个 tool（够用、可测、不臃肿）

| # | Tool | 功能 | 入参 | 出参 |
|---|---|---|---|---|
| 1 | **`recommend_coop_games`** | 核心：按场景返回九档分组榜单 + 避雷 | `players:int=2`、`horror:enum[none,mild,scary]=none`、`budget:enum[free,demo,paid,any]=any`、`mode:enum[online,local,any]=online`、`session:enum[short,medium,long]=medium`、`max_price_cny:int?`、`exclude_appids:list[int]=[]`、`limit:int=8`、`lang:enum[schinese,en]=schinese` | `{status, query, groups[], avoid_list[], meta{}}`；缺关键参数时返回 `{status:"need_more_info", questions[]}` |
| 2 | **`resolve_game`** | 名称/模糊描述 → appid + 候选（用于"我们想玩恐鬼症那种"） | `query:str`、`limit:int=5` | `[{appid, name, price_cny, review_tier, header_image}]` |
| 3 | **`get_game_card`** | 单款游戏"证据卡"：九档 + 简中好评率 + 标签（恐怖/合作）+ 人数 + 3 条中文评测摘录 + 避雷点 | `appid:int`、`include_quotes:int=3` | 4.1 的 schema |
| 4 | **`log_play`** | 记录"玩过/踩雷/想玩"，写入本地 `user_data.json`，下次自动排除或加权 | `appid:int`、`verdict:enum[played,avoided,want]`、`note:str?` | `{saved:true, excluded_now:int}` |

配套 **1 个 prompt**：`tonight_playbook`（"今晚玩什么"对话模板：先问人数/恐怖/预算/单局时长，再调 `recommend_coop_games`，最后按档位输出清单 + 避雷）。

**不用做的**（第一版）：Steam 登录/好友库、价格预测、成就、创意工坊、多语言、UI。

### 6.2 追问问题设计（一轮内问完，4 个问题封顶）

| 问题 | 选项 | 为什么必须有 |
|---|---|---|
| 今晚几个人？ | 2 / 3–4 / 5+ | 3–4 人与 2 人是完全不同的池子（双人成行 vs 深岩银河） |
| 恐怖能接受吗？ | 一点都不行 / 一点点（心理恐怖可以）/ 越吓越好 | 痛点里的第一分叉（tagid 1667/1721/3978） |
| 预算？ | 只玩免费（含 Demo）/ 50 元内 / 随便 | 免费游戏池与付费池的"好评率分布"差异极大 |
| 想玩多久？ | 一局 30 分钟内 / 一晚上 / 长期一起玩 | 决定推"关卡制"还是"养成/生存" |

（可选第 5 问：本地同屏还是各回各家？——影响 category 24/39 vs 38。）

### 6.3 后续迭代优先级

| 优先级 | 功能 | 理由 |
|---|---|---|
| **P0（MVP 内）** | 九档分组榜单、避雷榜、中文评测摘录、踩雷记忆（`log_play`） | 你原始痛点的直接解 |
| **P1** | Steam 新品节 Demo 专题（`category1=10`，实测 total=41854 可抓） | 季节性流量高峰，与"试 Demo 踩雷"完美贴合 |
| **P1** | 打折提醒（价格缓存 1–6h，每日 diff） | 高使用频率，成本低 |
| **P2** | 好友游戏库联动（SteamID + Web API Key，需用户配置） | 竞品 Sarg338 已做且更细（共同拥有 + 按拥有者数排序），**不要作为核心卖点** |
| **P2** | "可发群的清单"图片/纯文本一键分享 | 传播杠杆 |
| **P3** | 局域网/远程同乐（Remote Play Together）专门档 | 小众但精准 |
| **P3** | 多人偏好学习（记录"点开率/差评反馈"） | 需要用户量 |

---

## 7. 命名、重名与推广

### 7.1 重名检查（2026-10-28 实测）

| 平台 | 检索 | 结果 |
|---|---|---|
| **GitHub** | `"今晚玩什么" in:name,description,readme` | **10 条，其中直接撞车：`tinman00/tonight`** — 描述原文就是"**「今晚玩什么」— Steam 游戏库推荐 Agent：对话式推荐 + 玩家画像 + 行为反馈**，Rust/axum/SQLite"，★3，最近推送 2026-09-09 |
| GitHub | `今晚玩什么 in:name`（仅仓库名） | total=0（说明它靠描述，不靠名字） |
| GitHub | `what-to-play-tonight in:name` | total=1（`mierzyk/WhatBoardgameWePlayTonight`，2020，桌游，★0） |
| PyPI | `what-to-play-tonight`、`play-tonight` | **404，可用** |
| npm | `what-to-play-tonight`、`tonight-play` | **404，可用** |
| **App Store 中国区** | `今晚玩什么` | **无同名**（返回"小转盘/多选一/决定鸭"等决策类应用） |
| MCP 目录 | Smithery / mcp.so / Glama 搜 steam/game | 无同名（也没有联机推荐类，见 §1） |

**【推断】** 中文名"今晚玩什么"作为**产品名**仍可用（各平台无正式注册冲突），但作为**GitHub 仓库名/话题**已被一个同赛道项目占了语义位——搜索"今晚玩什么 Steam"时它可能排在你前面，且它做的正是"对话式推荐"。建议：**产品中文名保留**，**仓库名用英文**，README 首行同时写中英名。

### 7.2 英文仓库名建议

| 名称 | 说明 | 风险 |
|---|---|---|
| **`coop-night-picker`** | 直白、含 "coop night" 场景词，SEO 好 | 无重名 |
| **`game-night-mcp`** | 突出 MCP 身份，易被目录收录 | 通用词，star 竞争大 |
| **`what2play-tonight`** | 贴近中文名语义 | 与 `what-to-play-tonight` 近似，易混 |
| （备选）`steam-coop-tonight` | 明确的 Steam + 联机 + 今晚 | 较长 |

推荐：**`coop-night-picker`**（PyPI/npm/GitHub 均可用），包名 `coop-night-picker-mcp`。

### 7.3 推广渠道（按性价比排序）

| 渠道 | 打法 | 预期 |
|---|---|---|
| **Smithery / mcp.so / Glama / awesome-mcp-servers PR** | 上架 + 一句"中文、零 Key、今晚玩什么"卖点 | MCP 生态仍空（§1 A4），收录即占位 |
| **Reddit r/CoopGaming / r/mcp / r/Steam** | 发"我做了个按 Steam 评价档位分组的联机推荐 MCP" + 真实案例截图 | 精准人群，但英文社区对中文产品冷淡 |
| **NGA / 小黑盒 / 贴吧** | "周末联机避雷"贴 + 榜单实拍 | 中文核心用户，转化最直接 |
| **B 站 / 小红书** | 短视频："我用 AI 筛掉了 30 款垃圾联机 Demo"（过程 + 榜单） | 内容形态天然适合，可导流 GitHub |
| **V2EX 分享创造 / 即刻 / 少数派** | 开发者向：技术卡点（Steam 接口被封 → CDN + 离线快照）本身就是好文章 | 开发者口碑 |
| **Hacker News Show HN / Product Hunt** | 英文版 + 一张"九档分组"截图 | 长尾，可能被 MCP 目录二次收录 |

**推广素材的杀手锏**：一张"**同一款游戏，商店写'特别好评'，简中只要 83%**"的对比图——这是实测数据（英灵神殿 93.64% vs 83.37%），既真实又有传播力。

---

## 8. 总结：值不值得做、最大风险、第一步

### 8.1 值不值得做：**值得，但要做"决策层"而不是"数据层"**

| 维度 | 评估 | 依据 |
|---|---|---|
| 生态空位 | ✅ 好 | 【事实】GitHub `coop game mcp` 仅 5 个仓库、`game recommendation mcp` 仅 6 个；Smithery/mcp.so/Glama 上的 steam 类全是"数据工具"，**没有一个按评价档位分组做联机推荐**（§1.1） |
| 用户痛点真实性 | ✅ 合理 | 你的痛点（"先试 Demo 一个个踩雷"）在社区是高频话题（§2）；且"周末联机"是重复性场景，不是一次性需求 |
| 技术可行性 | ⚠️ 一半 | 商店数据（标签/类别/价格/Demo/商店页评测摘要）**实测可拿**；**评测正文接口实测拿不到**（§3.8），这是唯一硬伤 |
| 差异化空间 | ✅ 明显 | 九档分组 + 中文评测理由 + 避雷榜 + 零 Key，四点现有竞品都不全有（§1.3） |
| 商业化 | ❌ 不要想 | 【推断】MCP Server 本身没有付费通道（Claude Desktop 无内购），只能靠 GitHub star → 个人品牌/接私活，别为赚钱做 |
| 你的能力匹配 | ✅ 高 | Python + 有 ML 背景（做评测摘要/排序顺手），业余 2–3 周可出 MVP |

**一句话**：这是一个"**小而美、传播性强、但必须绕开数据封锁**"的项目。做成了是很好的作品集项目（MCP + 数据工程 + 中文 NLP），做不成也亏得起。

### 8.2 最大的风险：**不是技术，是数据通道**（按危害排序）

| 风险 | 级别 | 具体表现 | 缓解 |
|---|---|---|---|
| **① 评测接口拿不到** | 🔴 致命 | 【事实，限"未走代理"的网络；v1.1：用户本机经代理 `127.0.0.1:7890` 实测 `store.steampowered.com/api/appdetails` 返回 200】官方域名全部 **TLS 层 RST**（DNS 正常，间歇发作）；`steamstore-a.akamaihd.net/appreviews` 返回 **HTTP 302 + `Content-Length: 0`**，重定向到已被阻断的首页，6 种绕法（`-L`/HTTP1.1/伪造 Host/全套 Cookie/换参/换 appid）全失败，POST 直接 403（§3.4）。**这解释了你遇到的 `IncompleteRead`**：跟随 302 后读到一半被切断。而产品价值 70% 在评测 | 三条路：<br>a) 让**用户在支持地区的客户端/网络**运行（MCP 是本地进程，你在海外/朋友的机器上可能就通）；<br>b) 用**商店页 `appreviews` 摘要**（`summary_options` + `filter_options.rgLanguageOutliers`）**或搜索卡片 tooltip**（`data-tooltip-html` 里直接有"档位+百分比+评测数"，一次拿 100 款）+ 标签 + 推荐数，**放弃评测原文**，理由改为"玩家标签 + 简中好评率 + 商店简介"；<br>c) 自带**离线快照**（GitHub Actions 抓一次 200 款，JSON 随包发），"今晚玩什么"是**低频决策**，快照完全够用；<br>d) **【v1.1 采用】海外 GitHub Actions 定时抓取（含 appreviews 评测原文）→ 生成 SQLite 快照分发**，国内用户只读快照 |
| **② 依赖非官方 CDN 域名** | 🟠 高 | 【事实】现在能通的只有 `steamstore-a.akamaihd.net` 这类**非官方文档化**主机（§3.1），随时可能变/被限 | 所有抓取走**单一适配层** `steam_client.py`（一个 `BASE_HOSTS` 列表 + 自动探测 + 失败回退），**不能把 host 写死在业务代码里** |
| **③ ToS / 法律** | 🟠 高 | 【推断】批量抓取 Steam 页面违反 Steam 订阅者协议的可能性存在；把评测原话再分发有版权风险 | 只存**短摘录 + 原文链接**、非商业、公开仓库加免责声明、支持 `NO_FETCH=1` 离线模式；不缓存完整评测正文 |
| **④ 需求未被验证** | 🟡 中 | §2 的证据还在收集中（见下"证据缺口"）；"联机游戏推荐"在中国是内容饱和区，工具化未必有人用 | 先做出来给自己和朋友圈用两周，看是否每周真的打开；再决定是否推广 |
| **⑤ 竞品补齐** | 🟡 中 | Sarg338 已有 41 tools 且含 `steam_plan_coop_night`；它只需加一个"按档位分组"就抹平差异 | 差异化押在**中文 + 避雷榜 + 零 Key**（Sarg338 需要 Steam API Key，这是它的门槛） |
| **⑥ MCP 生态流量小** | 🟡 中 | 用户 = 装了 Claude Desktop/Cursor 的开发者，盘子小 | 同时做 CLI/GitHub Action 版（同一套 core），MCP 只是入口之一 |

### 8.3 第一步：72 小时内做完这 3 件事（按顺序，别跳）

> **【v1.1】** 72 小时产出的是"可演示原型"；完整 MVP 仍按 §0 估算为业余 2–3 周。详细步骤以《技术路径》为准。

| 步骤 | 做什么 | 产出 | 决策点 |
|---|---|---|---|
| **Day 1 上午** | **数据通路裁决**：三种网络/方式各测一次 `/appreviews/548430?json=1&language=schinese&num_per_page=0`（v1.1：appid 在路径里，不是 `appids=` 参数；同时检查代理软件是否把 Steam 设为直连）（如 ① 本机 ② 手机热点 ③ 云主机/朋友机器 ④ 任一 HTTP 代理） | 一页结论：通 / 不通 | **不通 → 立刻转 8.2①b/c 方案，别浪费时间调网络** |
| **Day 1 下午** | 用**离线快照**跑通 pipeline：`research/raw/` 已有 16 个 `appdetails` 原始 JSON（`research/raw/steam-appdetails-*.json`）→ 写 `build_snapshot.py` 抓 200 款 co-op 候选（`category1=998&category3=38`（或 `tags=3843`）+ `tags=1685` + `category1=10` 三路去重；v1.1：category 参数待实测） | `snapshot.json` + SQLite | 200 款抓取成功率 ≥ 90% → 商店层 OK |
| **Day 2** | 实现 `recommend_coop_games` + `get_game_card` 两个 tool（方案①：缺参返回 `need_more_info`），本地 `mcp dev` 用 Inspector 跑通 | 可演示的 server | 问"4 人/不恐怖/免费"能出九档榜单 = MVP 达标 |
| **Day 3** | README（含 §3.8 的"数据源现状"、免责声明）+ 3 张截图 + 一段 GIF；发 V2EX「分享创造」+ GitHub | 可推广的最小版本 | 有没有 5 个人 star/试用 → 决定继续或停 |

> 顺序很重要：**Day 1 上午的通路裁决是整个项目的 Go/No-Go**，不要先写代码。

### 8.4 我的证据缺口（诚实声明）

| 缺口 | 原因 | 影响 |
|---|---|---|
| §2 社区原文摘录 | 【v1.1】§2 已落盘（Reddit 9 条 + B 站 4 条原文），但B 站/小红书/贴吧/知乎 **需登录或强反爬**，Reddit 也需 OAuth | 需求侧结论偏"推断"，发布前建议你人工逛一圈 NGA/小黑盒确认 |
| `appreviews` 为何不通 | 已确认"所有 host 均 RST/空"，**未能定位根因**（是 SNI 阻断？IP 级？TLS 指纹？还是仅本网络环境）。**v1.1 补充：最可能的原因是代理软件（Clash）规则把 Steam 域名设为 DIRECT 直连，用户经代理实测 appdetails 可通** | 若你在别处能通，结论要改：产品按"全功能"做即可 |
| Smithery/mcp.so/Glama 的确切收录数与活跃度 | 站点为动态渲染，只能拿到抓取时的命中数 | 竞品数量级正确，具体数字可能浮动 |
| 中文评测语料规模 | 评测接口不通，无法实测中文语料 | §4 的关键词表来自推测 + 少量商店页可见文本，需实测校准 |

---

## 附：可用数据面速查（给实现阶段）

| 需求 | 接口（实测可用） | 关键字段 |
|---|---|---|
| 游戏基本信息 + 价格 | `GET https://steamstore-a.akamaihd.net/api/appdetails?appids=<id>&cc=cn&l=schinese` | `name/type/is_free/price_overview{currency,final,final_formatted}/categories[].id/genres/release_date/header_image/short_description/pc_requirements`、`demos{appid}`、`fullgame`（demo 才有）、`recommendations.total` |
| 九档 + 分语言好评率 | `GET https://steamstore-a.akamaihd.net/app/<id>/?l=schinese&cc=cn` → 正则 `data-featuretarget="appreviews"` 的 `data-props`（**HTML 转义，先 `html.unescape`**） | `summary_options.summaryDisplayed{ nReviews, nReviewsPositive, eReviewScore }`、`filter_options.rgLanguageOutliers[]`（含 `schinese` 的 `eReviewScore/nReviewsPositive/nReviews`）、`filter_options.nReviewsPositive/nReviewsNegative` |
| 单款游戏的玩家标签（含 id） | 同上商店页 | `InitAppTagModal(<appid>, [{"tagid":…,"name":…,"count":…}])` |
| 全站热门标签（找 ID） | `GET https://steamstore-a.akamaihd.net/tagdata/populartags/schinese` | 429 个 `{tagid,name}` |
| 商店类别（找 ID） | `GET https://steamstore-a.akamaihd.net/actions/ajaxgetstorecategories` | 73 个 `{categoryid,description,show_in_search}` |
| 候选池 | `GET https://steamstore-a.akamaihd.net/search/results/?query=&start=0&count=50&category1=998&category3=38&infinite=1`（v1.1：原写 category1=38 有误，category1 是"类型"，待实测） | `total_count`、`results_html` 内 `data-ds-appid` |
| 联机人数（替代方案） | `GET https://www.pcgamingwiki.com/w/api.php?action=parse&page=<Page>&prop=wikitext&format=json&formatversion=2` | `{{Network/Multiplayer\|online play players=4\|online play modes=co-op}}` |
| 评测正文（**当前不通**） | `GET https://store.steampowered.com/appreviews/<id>?json=1&language=schinese&filter=all&num_per_page=50` | `query_summary{review_score,review_score_desc,total_positive,total_negative}`、`reviews[]`（含 `votes_up`、`recommendationid` → 评测 URL） |

**九档映射（v1.1 已更正，以 §3.4 为准）**：`9=好评如潮, 8=特别好评, 7=好评, 6=多半好评, 5=褒贬不一, 4=多半差评, 3=差评, 2=特别差评, 1=差评如潮`。文档示例：TF2 `review_score=8`、`review_score_desc="Very Positive"`、672753/747091≈90.05%。
**⭐ 重要提醒（v1.1 更正）**：原文"`downloadableContent` 字段只给 Top-10% 游戏差评"无来源，已删除。`appreviews` 支持 `review_type=negative` 直接拉差评；避雷榜建议综合"差评关键词命中 + 简中差评率 + 全语言/简中档位差"。

**HTTP 客户端建议（v1.1 更正）**：原文的"LibreSSL 3.5.2 / 只有 curl 能通"不适用于用户环境（Windows + Python 3.12，`urllib`/`httpx` 经代理可通）。统一用 `httpx`；**关闭自动跟随重定向**，以便识别 `appreviews` 的 302 拦截；代理通过环境变量或配置项显式传入。
