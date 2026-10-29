# Task 3 实现报告

## 状态
完成。已实现本地 FastMCP 服务、用户游玩日志和离线测试。

## 变更文件
- `src/coop_night_picker/server.py`：4 个工具、`tonight_playbook` prompt、快照缺失处理、分组 Markdown、原子日志写入、Steam 搜索回退、MCP 依赖提示。
- `pyproject.toml`：增加 `coop-night-picker` console entry point。
- `tests/test_server.py`：覆盖追问、空快照、筛选分组、日志排除、本地解析、证据卡和 Markdown。

## 测试与输出
- `pytest -q`：9 passed

## 关注事项
- `data/user_log.json` 和 SQLite 快照路径沿用 `.gitignore`；测试通过环境/模块路径覆盖使用临时文件。
- `resolve_game` 仅在本地结果不足时调用 Steam；网络失败写入结构化 `meta.network`。

## Fix Round 1

- 修复日志路径默认参数在导入时绑定的问题，支持运行时覆盖路径。
- `log_play` 返回 `excluded_appid`，并保持原子追加。
- 证据卡按好评/差评分别查询，确保差评不会被好评挤出。
- 避雷榜复用恐怖、预算、人数、模式筛选条件。
- 首轮追问补充游玩时长，预算选项统一为 `free_or_demo`、`under_50`、`any`。
- Markdown 增加推荐评测原句和原评 URL，并展示模式。

验证：`pytest -q` → 9 passed。

## Fix Round 2

- `session_duration` 保留在工具签名，并写入返回的 `query` 与 `meta`；当前作为提示性条件（快照无时长字段），confirmed 后不会重复追问。
- Markdown 模式标签改为 online、local/co-op、online/local。
- 推荐评测和避雷差评均展示“来自玩家评测”、有用票数与原评 URL；避雷原因使用中文连接文本。

验证：`pytest -q` 与 `python -m unittest discover -s tests -q` 均通过（9 tests）。

## Final evidence wording fix

- 避雷负面引用明确标注“避雷证据（来自玩家评测）”。
- 增加离线断言，确保带负面引用的避雷榜 Markdown 包含该归因。

验证：pytest 与 unittest 均 9 tests 通过。
