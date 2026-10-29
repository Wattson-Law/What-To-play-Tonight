# 本地验收记录（2026-10-29）

在项目根目录 `F:\个人项目\暂定\coop-night-picker` 执行：

```powershell
$env:PYTHONPATH="src"
python -m pytest -q
```

结果：`10 passed, 1 skipped`（包含 `tests/test_acceptance.py` 的离线四工具验收；仅 FastMCP 注册断言因依赖不可导入而跳过，不访问 Steam）。

```powershell
python scripts/verify_snapshot.py --db tmp-fixtures/valid.sqlite
```

结果：退出码 `0`，输出游戏、引用和评级统计；命令只读数据库。

```powershell
python scripts/verify_snapshot.py --db tmp-fixtures/missing.sqlite
```

结果：输出 `missing database`，退出码 `1`，没有创建或修改文件。

```powershell
python scripts/probe_network.py --json
```

实际结果：退出码 `0`；官方直连端点记录 `classification=error`，CDN 组合部分为 `ok` 或 `redirect_trap`。该命令仅作诊断，不影响离线测试和构建。

```powershell
python -m coop_night_picker.server
```

结果：启动本地 FastMCP 服务（需要安装 `mcp[cli]`）；未安装时给出明确安装提示。
