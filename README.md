# coop-night-picker

“今晚玩什么”是一个本地优先的 Steam 合作游戏推荐 MCP 服务。它读取提交到仓库的 SQLite 快照，按人数、恐怖接受度、预算和在线/本地模式筛选，并保留玩家评测证据与避雷榜。

## 安装与运行

需要 Python 3.11+（CI 使用 Python 3.12）。在项目根目录执行：

```powershell
python -m pip install -e .
python -m coop_night_picker.server
```

项目固定使用 MCP SDK 1.x（`mcp[cli]>=1.9,<2`），以保证 FastMCP 导入路径兼容。

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

也可以设置 `COOP_NIGHT_PICKER_DB` 和 `COOP_NIGHT_PICKER_USER_LOG` 指向其他本地文件。

## 快照与离线验证

从 Steam 构建最多 300 个游戏的快照，并尝试采集评测：

```powershell
python scripts/build_snapshot.py --limit 300 --with-reviews
python scripts/verify_snapshot.py
```

没有网络时可先生成一个用于校验的最小 fixture，再运行校验：

```powershell
python scripts/create_offline_fixture.py --output tmp-fixtures/valid.sqlite
python scripts/verify_snapshot.py --db tmp-fixtures/valid.sqlite
```

`tmp-fixtures/` 是本地临时目录并被 `.gitignore` 忽略；若要测试 `build_snapshot.py --offline-fixture`，请提供自己的 Steam HTML/JSON fixture 文件夹。
空数据库只适合本地冒烟测试，可显式使用 `python scripts/verify_snapshot.py --db empty.sqlite --allow-empty`；CI 正常校验会拒绝空快照。

网络诊断是可选的，不会改变快照：

```powershell
python scripts/probe_network.py --json
```

Steam 的 `appreviews` 可能被区域网络、代理或服务端策略拦截；构建器会保留商店页和已有字段，评测缺失时仍能生成可验证快照。服务运行时只查询本地快照；`resolve_game` 在本地结果不足时才尝试 Steam 搜索。

## MCP 工具与提示词

- `recommend_coop_games`：先一次性收集人数、恐怖接受度、预算、游玩时长，再返回按评价分组的推荐和避雷榜。
- `get_game_card`：返回单个游戏的价格、模式、评级、简中评级、评测引用和避雷理由。
- `resolve_game`：本地名称搜索优先，结果不足时补充 Steam 搜索，并返回网络状态元数据。
- `log_play`：把 `played`、`avoided` 或 `wishlist` 写入本地用户日志；已玩和避开的 AppID 会自动排除。
- `tonight_playbook`：指导 MCP 客户端最多提问一轮并渲染 Markdown 结果。

## 项目布局

- `src/coop_night_picker/`：数据库、Steam 客户端、解析器、MCP 服务。
- `scripts/`：快照构建、快照校验、网络探测。
- `data/games.sqlite`：可提交的离线快照；`data/user_log.json` 和 HTTP 缓存仅保留在本机。
- `tests/`：纯离线单元与验收测试。
- `docs/ACCEPTANCE.md`：本地验收命令及结果。
