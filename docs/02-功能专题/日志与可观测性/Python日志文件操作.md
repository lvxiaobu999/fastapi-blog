# Python 日志文件操作手册

## 1. 本项目的文件结构

本地或单机文件模式把所有达到 `LOG_LEVEL` 的事件写入同一个日期文件：

```text
logs/
└── 2026-08-07.log
```

DEBUG、INFO、WARNING、ERROR、CRITICAL 仍保留在每条日志的 `level` 字段中，不需要映射为物理目录。这样可以按 request_id 直接阅读 INFO → WARNING → ERROR 的完整时间线；需要只看 ERROR 时使用文本搜索或日志平台字段查询。

## 2. 配置

开发环境默认使用 `LOG_TO_FILE=false` 直接查看终端。需要学习或验证文件轮转时，临时使用：

```dotenv
LOG_LEVEL=DEBUG
LOG_FORMAT=text
LOG_TO_FILE=true
LOG_DIRECTORY=logs
LOG_MAX_BYTES=20971520
LOG_FILES_PER_DAY=5
LOG_RETENTION_DAYS=14
```

单文件最大 20 MiB，达到上限后生成 `.1`～`.4`，因此每天最多约 100 MiB。进入新的 UTC 日期并首次写日志时，会删除 14 天保留窗口以外的文件。

修改环境文件后必须重启应用：

```powershell
Set-Location D:\study\python\fastapi-blog
uv run fastapi dev app/main.py
```

## 3. 查看日志

查看文件和容量：

```powershell
Get-ChildItem .\logs -Recurse -File |
    Select-Object FullName, Length, LastWriteTime
```

持续查看当天日志：

```powershell
$today = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
Get-Content ".\logs\$today.log" -Wait -Tail 50
```

只查看当天 ERROR/CRITICAL：

```powershell
$today = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
Select-String -Path ".\logs\$today.log*" -Pattern " ERROR | CRITICAL "
```

跨当前文件和大小轮转文件搜索请求 ID：

```powershell
Select-String -Path .\logs\*.log* -Pattern "request_id值"
```

应用按 UTC 日期命名，因此北京时间凌晨可能仍写入前一个 UTC 日期，这是多服务器统一时间边界的预期行为。

## 4. 临时覆盖

PowerShell 环境变量优先于 `.env`：

```powershell
$env:LOG_LEVEL = "DEBUG"
$env:LOG_TO_FILE = "true"
$env:LOG_DIRECTORY = "logs-debug"
$env:LOG_MAX_BYTES = "10485760"
$env:LOG_FILES_PER_DAY = "3"
uv run fastapi dev app/main.py
```

调试后清除覆盖：

```powershell
Remove-Item Env:LOG_LEVEL, Env:LOG_TO_FILE, Env:LOG_DIRECTORY
Remove-Item Env:LOG_MAX_BYTES, Env:LOG_FILES_PER_DAY
```

## 5. 轮转和删除规则

同一天的文件达到上限后：

```text
logs/2026-08-07.log
logs/2026-08-07.log.1
logs/2026-08-07.log.2
logs/2026-08-07.log.3
logs/2026-08-07.log.4
```

超过 `LOG_FILES_PER_DAY=5` 后删除最旧备份。日期保留清理不是后台定时任务，而是在切换日期后的首次写入时执行。如果需要严格定时清理，应使用日志平台 retention 或受控的运维任务。

手动清理前先停止应用并确认目录：

```powershell
Resolve-Path .\logs
Get-ChildItem .\logs -Recurse -File | Measure-Object Length -Sum
```

确认后可清空内容但保留目录：

```powershell
Get-ChildItem .\logs -Recurse -File -Filter "*.log*" | Clear-Content
```

不要在应用正在写入时删除或清空当天文件。升级前遗留的 `logs/app.log` 不再写入，停止旧进程并确认不再需要后可自行归档或删除。

## 6. 常见问题

- 找不到独立 error 文件：现在有意只保存一个完整日期文件，使用 level 字段筛选错误。
- 修改配置没有生效：检查 `Get-ChildItem Env:LOG_*` 是否覆盖 `.env`，然后重启应用。
- 没有 aiosqlite DEBUG：项目将 `aiosqlite` 和 `sqlalchemy.engine` 单独限制为 WARNING，避免底层数据库日志刷屏。
- 文件不在预期位置：`LOG_DIRECTORY` 相对于启动进程的工作目录，应从项目根目录启动。
- 多 Worker 轮转报错：本地大小轮转不适合多个进程竞争，应切换为 stdout JSON。

## 7. 生产环境

容器生产环境推荐：

```dotenv
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_TO_FILE=false
```

FastAPI 写 stderr/stdout，Docker、Fluent Bit、Vector 或 Promtail 负责采集，Loki/ELK 负责检索、告警和 retention。生产日志不依赖应用服务器本地磁盘。

## 8. Git

运行日志不提交 Git。仓库已忽略：

```gitignore
logs/
*.log
*.log.*
```
