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
