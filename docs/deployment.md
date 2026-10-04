# 配置、部署与恢复

## 本机开发

用 `.venv` 安装项目依赖，再运行 `scripts/start-local.ps1`。脚本先 `alembic upgrade head`，启动一个 Worker 和 API。默认 SQLite 数据、附件和独立删除账本放在 `.data/`。直接运行 Uvicorn 前也必须先迁移。

`-UseDeepSeek` 只读取进程继承的 `DEEPSEEK_API_KEY`，设置本轮 `AI_MODE=deepseek`。不要把密钥填进手机设置或源码。停止脚本后 AI 模式不影响下一次独立启动。

## AI 模式

| 配置 | 能力 |
| --- | --- |
| `AI_MODE=disabled` | 可靠录入、同步、原文日记、关键词搜索；附件待 AI 处理 |
| `AI_MODE=deepseek` | 文本事件、日记、问答、伙伴和周回顾；`DEEPSEEK_MODEL=deepseek-flash` |
| `AI_MODE=bailian` | 上述能力 + ASR / 视觉 / 向量；需要 `DASHSCOPE_API_KEY` |

百炼默认模型为 `qwen-plus`、`qwen3-asr-flash`、`qwen3-vl-flash`、`text-embedding-v4`，嵌入维度 1024。模型选择在服务端配置；固定版本号须在真实质量基线建立后选择。当前没有百炼真实调用结果，不应据接口实现宣称多模态质量通过。

模型切换后可从记录详情重新处理。DeepSeek 不伪装成 ASR / 视觉服务，原始音频和图像始终保留。

## 生产容器

需要 Linux Docker Compose、域名 HTTPS 反向代理和可用的 OSS。生产 `.env` 必须设置：

- `ENVIRONMENT=production`
- 随机且至少 32 字符的 `JWT_SECRET`
- 持久保存的 Fernet `ENCRYPTION_KEY`
- 内测 `REGISTRATION_TOKEN`
- 随机 `POSTGRES_PASSWORD`（建议 URL 安全字符；如有特殊字符，数据库 URL 需编码）
- `STORAGE_BACKEND=oss`、OSS 端点 / 桶 / 密钥
- AI 模式与对应密钥，以及按实际账单调整的固定费用和单价

用 Python `secrets.token_urlsafe` 生成随机密码 / JWT，用 `cryptography.fernet.Fernet.generate_key()` 生成加密密钥，直接写入权限受限的配置文件或服务密钥管理器；不放进 Git。配置参考 `.env.example`。

```sh
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/health
```

PostgreSQL 16 + pgvector 只在容器网络内暴露；迁移成功后才启动 API / Worker；服务失败自动重启。API 默认只绑定宿主机 127.0.0.1:8000，用 HTTPS 反向代理转发。反向代理设置请求大小上限、登录限流和安全的转发头信任范围。正式 APK 必须使用 HTTPS 地址。

配置 `FIXED_MONTHLY_COST_YUAN` 应包含服务器、存储和预期备份支出；单价是估计值，运行预算统计不连接阿里云 / DeepSeek 实时账单。当前 80 元固定支出是默认假设，需要替换为实际套餐费用。240 元预警，300 元总上限，另预留 20 元。

## 每日备份

备份脚本需要宿主机 Python 环境、项目包、`pg_dump` / `pg_restore`、PostgreSQL 连接配置、OSS 和显式加密密钥。可以通过宿主机定时任务每日执行：

```sh
python scripts/backup.py
```

脚本先 `pg_dump` 到进程内存，再加密上传独立 OSS `backups/`。文件名带 UTC 时间。为备份对象设置保留策略；`deletion-ledger/` 不应因备份轮替删除。保存密钥的离线副本，丢失密钥无法恢复附件或数据库备份。

生产建议备份与主数据使用独立存储账户 / 桶及权限；当前脚本使用配置桶的独立前缀，跨账户独立桶的备份复制需在部署侧配置。备份计划尚未在真实 OSS 环境执行。

## 恢复演练

1. 停止 API 和 Worker，在隔离环境验证备份和密钥，保留当前数据库副本。
2. 配置目标 PostgreSQL、相同附件存储和加密密钥。
3. 执行 `python scripts/backup.py --restore backups/具体文件.dump.enc`。
4. 脚本还会读取数据库备份之外的删除账本，重新清除备份中已删除记录。
5. 验证 `/health`、账户隔离、附件哈希、人工日记段落、删除后检索失效，确认后再恢复服务。

恢复期间不能启动对外读取服务；数据库恢复完成到删除账本应用完成之间不允许用户访问。不要用旧备份覆盖独立删除账本。SQLite 开发数据恢复也必须同时保留当前 `.data/deletion-ledger/`。

## 发布和下一环境

Debug APK 只供个人联调。正式发布应配置独立签名，固定依赖与模型版本，完成真机和生产恢复验收，再邀请朋友使用邀请码注册。当前仓库没有部署到公网、没有开通收费服务，也没有提交到远程 Git 仓库；CI 文件已准备，实际运行仍需仓库托管环境。
