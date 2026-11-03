# coop-night-picker（今晚玩什么）

“今晚玩什么”是一个基于 **Steam 鉴赏家（Curators）买手精选 + 真实玩家评测双轨证据** 的合作游戏推荐 MCP 服务。它不再盲目抓取粗糙的 Steam 搜索生数据，而是聚合头部中文鉴赏家（小黑盒、中国鉴赏家同好会、游饼、中国玩家鉴赏组、UP鉴游组、好玩✔Fun、奶牛关）与全球联机垂直权威（Co-Optimus）的人工推荐榜单与百字锐评，在 GitHub Actions 云端自动构建快照，并支持按人数、恐怖接受度、预算、联机模式和玩法标签（如“独立”“Demo”“生存”“解谜”“肉鸽”）精准筛选。

## 核心特色

1. **汇总人类的汇总（买手制精选）**：优先推荐被多位 Steam 鉴赏家共同列入榜单的联机好游与新品节试玩 Demo；哪怕是刚上线、0 条普通玩家评论的新 Demo，也有鉴赏家人工试玩锐评背书。
2. **双轨推荐理由 + 避雷榜**：同时保留 `[鉴赏家·名称] 人工锐评` 与高赞简中玩家原话子串；鉴赏家标记“不推荐”或差评集中吐槽掉线/半成品的游戏自动进入避雷榜。
3. **纯 GitHub Actions 云端构建 + 零本地快照维护**：数据构建与评测抓取全部在 GitHub Actions 海外 Runner 自动运行；MCP 运行时若本地无快照，会自动拉取 GitHub 云端最新快照或实时请求 Steam 鉴赏家接口。
4. **支持玩法标签与真实联机人数筛选**：`recommend_coop_games` 支持 `tag="独立"` / `"Demo"` / `"生存"` / `"解谜"` / `"肉鸽"` / `"沙盒"` 等标签过滤，并自动提取支持联机人数上限。

## 安装与运行

需要 Python 3.11+（CI 使用 Python 3.12）。在项目根目录执行：

```powershell
python -m pip install -e .
python -m coop_night_picker.server
```

MCP 客户端配置示例（`PYTHONPATH` 使用绝对路径）：

```json
{
  "mcpServers": {
    "coop-night-picker": {
      "command": "python",
      "args": ["-m", "coop_night_picker.server"],
      "env": {
        "PYTHONPATH": "F:\\个人项目\\暂定\\coop-night-picker\\src"
      }
    }
  }
}
```

可选环境变量：
- `COOP_NIGHT_PICKER_CLOUD_DB_URL`：自定义云端快照地址（默认读取本仓库 GitHub Actions 产出的最新快照）。
- `COOP_NIGHT_PICKER_DB` / `COOP_NIGHT_PICKER_USER_LOG`：自定义数据库或游玩记录文件路径。

## GitHub Actions 云端自动构建

仓库内置 `.github/workflows/update_snapshot.yml`：
- 当 `main` 或 `implementation` 分支的代码更新推送、每周一定时（`cron: "17 3 * * 1"`）或手动点击 `Run workflow` 时，GitHub Actions 会在海外节点自动执行：
  ```bash
  python scripts/build_snapshot.py --limit 120 --with-reviews
  python scripts/verify_snapshot.py
  ```
- 无需在本机运行爬虫或维护本地 `data/games.sqlite`。

## MCP 工具与提示词

- `recommend_coop_games`：先一次性收集人数、恐怖接受度、预算、游玩时长（可选传入 `tag` 如 `"独立"`、`"Demo"`），返回按评价档位分组、优先展示多位鉴赏家背书的推荐清单与避雷榜。
- `get_game_card`：返回单款游戏的完整证据卡（含鉴赏家背书名单、全语言/简中双轨好评率、鉴赏家与玩家原话摘录、避雷原因）。
- `resolve_game`：优先检索已收录游戏，不足时补充 Steam 搜索。
- `log_play`：记录 `played`、`avoided` 或 `wishlist`；已玩和避雷游戏自动从后续推荐中排除。
- `tonight_playbook`：引导客户端最多追问一轮并输出带鉴赏家背书与避雷榜的结果。
