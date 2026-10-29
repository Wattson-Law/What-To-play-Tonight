# 本地验收记录（2026-10-29）

在项目根目录 `F:\个人项目\暂定\coop-night-picker` 执行：

```powershell
$env:PYTHONPATH="src"
python -m pytest -q
```

结果：`16 passed, 1 skipped`（包含 `tests/test_acceptance.py` 的离线四工具验收和数据层回归测试；仅 FastMCP 注册断言因依赖不可导入而跳过，不访问 Steam）。

交互裁决：方案正文和产品交互草案对追问数量有差异，当前实现采用四问——人数、恐怖接受度、预算和游玩时长；`session_duration` 作为 advisory 元数据保留，不参与 SQLite 过滤。

```powershell
python scripts/create_offline_fixture.py --output tmp-fixtures/valid.sqlite
python scripts/verify_snapshot.py --db tmp-fixtures/valid.sqlite --allow-empty
```

结果：退出码 `0`，输出 `games=1 quotes=1 tiers={9: 1}`；命令只读数据库。

重复执行生成器两次后仍输出同样的 `games=1 quotes=1 tiers={9: 1}`，说明 fixture 生成是幂等的。

```powershell
python scripts/verify_snapshot.py --db tmp-fixtures/missing.sqlite
```

结果：输出 `missing database`，退出码 `1`，没有创建或修改文件。

小型 fixture 使用 `--allow-empty` 旁路最低 50 条及样本分布要求；生产快照正常校验会拒绝空或少于 50 条、缺少免费/Demo/恐怖/非恐怖样本的数据库。

```powershell
python scripts/probe_network.py --json
```

实际结果：退出码 `0`；脚本默认执行直连官方、直连 CDN、代理官方、代理 CDN 四组探测，并在代理下的 `appreviews` 失败时输出 Clash 规则和 Fallback Mode 提示。该命令仅作诊断，不影响离线测试和构建。

```powershell
python -m coop_night_picker.server
```

结果：启动本地 FastMCP 服务（需要安装 `mcp[cli]`）；未安装时给出明确安装提示。

项目依赖固定为 `mcp[cli]>=1.9,<2`，避免 SDK 2.x 改变 FastMCP 导入路径。
