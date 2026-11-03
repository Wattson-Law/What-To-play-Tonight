# 「今晚玩什么」方案 B 设计文档：GitHub Actions 云端鉴赏家聚合 + 免本地快照架构

- **日期**：2026-11-02
- **仓库**：`https://github.com/Wattson-Law/What-To-play-Tonight`
- **状态**：待确认（Brainstorming 设计稿 v2）

---

## 1. 仓库现状诊断（2026-11-02 实查 GitHub 仓库）

通过检查 `Wattson-Law/What-To-play-Tonight` 仓库与 GitHub API，发现当前存在三个关键问题：
1. **GitHub Actions 至今 0 次运行（`workflow_runs: 0`）**：
   - 仓库当前默认分支仍是 `implementation`（首次推送分支），且 `.github/workflows/update_snapshot.yml` 仅配置了 `schedule`（每周一）和手动 `workflow_dispatch`，**未配置代码推送触发**，导致云端任务从未跑起来。
2. **候选池靠盲抓 Steam 搜索生数据，缺少“买手筛选”**：
   - `build_snapshot.py` 直接抓 `search/results`（含大量粗糙 Demo），新 Demo 缺乏评论时既无质量把关也无推荐理由。
3. **之前在本地生成并提交 `data/games.sqlite` 违背了云端部署初衷**：
   - 数据抓取（鉴赏家榜单 + 商店详情 + 海外节点直连 `appreviews`）应完全交给 **GitHub Actions 云端**执行，本机不生成、不维护本地快照文件；MCP 运行时直接读取云端产物或实时调用鉴赏家接口。

---

## 2. 方案对比（Approaches）

| 方案 | 做法 | 优劣权衡 | 结论 |
|---|---|---|---|
| **方案 A：B 站盘点视频抓取** | 抓 B 站视频简介/分P，反查 Steam AppID | 中文俗称匹配 AppID 易出错 | 不采用 |
| **方案 B（云端版）：GitHub Actions 抓取 Steam 鉴赏家（Curators）精选 + 海外评测 + MCP 云端/实时直读** | 1) 本机不生成本地快照；<br>2) GitHub Actions 自动抓取 **Steam 联机/独立/新品节 Demo 鉴赏家精选榜单（含人工百字锐评与避雷）+ 海外直连玩家评测**；<br>3) MCP 服务支持直接拉取 GitHub 云端最新数据 / 实时查询鉴赏家接口 | 天然带 `appid` 与标签零误配；新 Demo 也有鉴赏家试玩锐评；完全跑在 GitHub Actions 云端，无需本机跑爬虫写库 | **已选定** |
| **方案 C：多平台大聚合** | 同时抓 B 站 + 小黑盒 + Reddit + 鉴赏家 | 跨平台实体对齐过重 | 暂不采用 |

---

## 3. 核心架构与改动点

### 3.1 彻底去掉“本机生成快照”，全量交由 GitHub Actions 运行（`.github/workflows/update_snapshot.yml`）
- **触发机制升级**：
  - 在 `schedule` 与 `workflow_dispatch` 基础上，增加对 `main` / `implementation` 分支核心代码变更的自动触发，推送后 GitHub Actions 立即在海外 Runner 启动抓取。
  - 同步将 `main` 与 `implementation` 分支对齐，保证无论默认分支是哪个都能触发并产出最新云端数据。

### 3.2 升级为「Steam 鉴赏家（Curators）买手制」管线（`constants.py`、`steam_client.py`、`extractor.py`、`build_snapshot.py`）
- **鉴赏家精选池（`CURATOR_SOURCES`）**：
  - 聚合联机垂直鉴赏家（如 `Co-Optimus`）+ 中文高关注独立游戏/新品节试玩鉴赏家（通过 `curators/ajaxgetcurators` 与精选白名单获取）。
  - 调用 `curator/<clanid>/ajaxgetfilteredrecommendations` 拉取合作标签（`3843` 在线合作、`1685` 合作、`3841` 本地合作）及 Demo 推荐。
- **双轨理由与避雷证据**：
  - **鉴赏家锐评**：直接提取 `.recommendation_desc` 人工百字简评、推荐态度（`推荐` / `情报` / `不推荐`）和原帖链接；哪怕是 0 评论的新 Demo，也有鉴赏家试玩结论。
  - **玩家真实评测**：在 GitHub Actions 海外节点直连 `appreviews`（无国内 302 拦截），提取高赞好评与差评原话子串。
- **真实标签、独立游戏与人数补齐**：
  - 解析并保留完整标签（“独立”“生存”“解谜”“肉鸽”“局域网”等）与 `max_players` 真实联机人数上限。

### 3.3 MCP 服务端支持云端数据拉取与实时鉴赏家回退（`server.py`）
- **免本地构建**：当本地不存在快照或启用云端模式时，MCP 自动从 GitHub 仓库 `Wattson-Law/What-To-play-Tonight` 拉取 GitHub Actions 生成的最新快照到内存/临时缓存，或直接实时请求 Steam 鉴赏家接口。
- **新增 `tag` 与买手优先排序**：
  - `recommend_coop_games` 新增 `tag` 参数（如 `tag="独立"`、`tag="Demo"`、`tag="生存"`）。
  - 排序优先展示 **`curator_count`（多位鉴赏家共同推荐）** 的高分游戏与新 Demo，并在返回卡片中标注 `🏅 鉴赏家精选（N位推荐）` 与鉴赏家评语。
