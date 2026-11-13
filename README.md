# coop-night-picker（今晚玩什么）

“今晚玩什么”是一个把 **Steam 鉴赏家人工背书 + 真实玩家评测原话** 汇成离线快照，再通过 **MCP 工具** 和 **KOOK 群机器人** 两个入口输出「今晚开黑玩什么」的服务。

它不抓 Steam 搜索生数据（那会把一堆没评分的垃圾捞上来），而是**汇总别人的汇总**：头部中文鉴赏家的榜单与百字锐评、热门愿望单、新发售榜、Demo 榜，交叉命中后才进入候选池。

## 一、数据从哪来

| 来源 | 作用 | 实现 |
|---|---|---|
| 8 条 Friendslop 检索式 | 零人工干预捞“妖精搞笑”联机小游戏 | `constants.FRIENDSLOP_FEEDS`：常见公式 = `3843 在线合作` + (`4136 欢乐` / `1719 喜剧` / `3968 物理` / `7178 派对`)，外加 `filter=popularwishlist`、`sort_by=Released_DESC`、`category1=10&category3=38`（Demo 榜） |
| 8 位鉴赏家 | 人类买手背书 + 锐评原话 | `constants.CURATOR_SOURCES`：小黑盒盒娘后援会、中国鉴赏家同好会、游饼、中国玩家鉴赏组、UP鉴游组、好玩✔Fun、CowLevel.Net、Co-Optimus |
| Steam `appdetails` | 价格 / 分类 / 支持人数 / 简介 | `steam_client.get_appdetails`（官方站 → `steamstore-a.akamaihd.net` 回退，不跟 30x） |
| Steam 商店页 + `appreviews` | 标签、全语言/简中好评率、玩家原话 | `extractor` 解析 `InitAppTagModal`、`data-featuretarget="appreviews"`、评测 JSON；评测 302 时降级为「仅商店元数据」 |

关键判定：
- **鉴赏家锐评含负面词**（阉割/赶工/快逃/半成品/骗钱…）→ 该条翻转为 `not_recommended`、`voted_up=0`，游戏被压到低档并进避雷榜。这是把《生化危机3》这类“大厂旧作”挤出推荐位的关键机制。
- **每条玩家引文必须是原始评测的真子串**（6–80 字），由 `extractor` 断言，`verify_snapshot.py` 复核。
- 鉴赏家锐评里的“每日福利游戏激活码”“求关注~”等自 promo 文案会被清洗掉。

## 二、云端流水线（GitHub Actions，零本地维护）

```
push / 每周一 03:17 UTC / 手动 Run workflow
   ↓  .github/workflows/update_snapshot.yml
build_snapshot.py --limit --with-reviews   →  data/games.sqlite
verify_snapshot.py                          →  结构 / 档位 / 引文子串校验
git commit data/games.sqlite [skip ci]      →  main（先 pull --rebase -X theirs 再推）
```

当前快照：150 款游戏、182 条引文，7/7 目标妖精小游戏（逢考弊过、Dear Passengers、潜艇菜鸟团、Fart Together、PeeVPee、SCAM Line、青蛙小队）全部自动命中。

## 三、消费端 A：MCP 工具

`python -m coop_night_picker.server`（FastMCP）

- `recommend_coop_games(horror, budget, players, mode, tag, confirmed, limit_per_tier)`：按 9 档评价分组 + 避雷榜 + Markdown 预览；`confirmed=false` 时返回一次性追问。
- `get_game_card(appid, include_quotes)`：全语言/简中双轨好评率、鉴赏家背书名单、正负引文、避雷原因。
- `resolve_game(query, limit)`：先本地 SQLite，不足再打 Steam 搜索。
- `log_play(appid, status, note)`：`played` / `avoided` / `wishlist`，前两者自动从后续推荐里排除。
- `tonight_playbook`：提示词，约束客户端最多追问一轮。

运行时若本地没有 `data/games.sqlite`，会自动拉 `raw.githubusercontent.com/.../main/data/games.sqlite` 到临时目录（`COOP_NIGHT_PICKER_DISABLE_CLOUD=1` 可关）；两者都没有时才走实时鉴赏家抓取。

## 四、消费端 B：KOOK 机器人

`.github/workflows/kook_bot.yml` + `scripts/run_kook_bot.py`（WebSocket 网关 + REST 轮询双通道，避免漏消息）

- **在线策略**：cron 每小时触发、`cancel-in-progress: false` 排队串行，每次跑 5h17m（`KOOK_MAX_RUNTIME_SECONDS=19000`），前一次结束下一次立刻接棒 → 24/7；`push` 到 `main` 也会立刻拉起一轮。
- **触发**：`@机器人`、角色提及 `(rol)...`、或裸说 `今晚玩啥 / 玩什么 / 推荐 / 联机游戏`。
- **对话**：条件不足时只追问缺的字段（人数/恐怖接受度/预算），补齐后直接出结果；`4人 不恐怖 50以内` 这种一句带全的直接推荐。
- **其它指令**：`搜 <名字>`、`详情 <appid>`、`玩过/避雷 <appid>`。
- **可靠性**：回复按 1800 字符分片发送（KOOK 有长度上限，之前 3979 字的整条回复会被拒收且无报错）；跳过 15 分钟以上的旧消息，重启不会补答昨晚的聊天；每片打印 `http=/code=/len=` 便于排查。
- **密钥**：`KOOK_TOKEN`（Actions Secret，Libsodium SealedBox 加密上传）；可选 `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`，缺省用 `GITHUB_TOKEN` 打 GitHub Models 做兜底语义解析。

## 五、目录

```
src/coop_night_picker/
  constants.py     # 档位映射、标签字典、FRIENDSLOP_FEEDS、CURATOR_SOURCES、负面词表
  steam_client.py  # HTTP 适配层：主机回退、磁盘缓存、限速重试、绝不跟随 30x
  extractor.py     # 解析鉴赏家列表 / 商店页 / 评测 JSON，生成引文与避雷原因
  db.py            # SQLite schema、upsert、search、recommend、avoid list
  server.py        # FastMCP 工具 + 云端快照回退 + Markdown 渲染
  kook_bot.py      # 意图解析、会话状态、分片回复、WS+REST 双通道
scripts/
  build_snapshot.py / verify_snapshot.py / create_offline_fixture.py
  probe_network.py  # 四组网络/代理组合探针，输出 Clash 规则与降级建议
  run_kook_bot.py
data/games.sqlite   # 由 CI 生成并提交；本地可缺省
```

## 六、本地安装

```powershell
python -m pip install -e .
$env:PYTHONPATH="src"
python -m unittest discover -s tests -q   # 26 tests
python -m coop_night_picker.server         # MCP
```

MCP 客户端配置：

```json
{
  "mcpServers": {
    "coop-night-picker": {
      "command": "python",
      "args": ["-m", "coop_night_picker.server"],
      "env": { "PYTHONPATH": "F:\\个人项目\\暂定\\coop-night-picker\\src" }
    }
  }
}
```

环境变量：`COOP_NIGHT_PICKER_DB`、`COOP_NIGHT_PICKER_USER_LOG`、`COOP_NIGHT_PICKER_CLOUD_DB_URL`、`COOP_NIGHT_PICKER_DISABLE_CLOUD`、`STEAM_PROXY`、`STEAM_HOST`、`KOOK_TOKEN`、`KOOK_MAX_RUNTIME_SECONDS`、`KOOK_MAX_MESSAGE_AGE_SECONDS`。
