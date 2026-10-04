# SmartDiary · 拾记

原生安卓个人日记：先可靠留下文字、语音和照片，再生成有来源的日记，沿着人物、主题和事情的后续找回经历。

仓库已包含可构建的 Kotlin / Compose 客户端、FastAPI 服务端、数据库迁移、持久化 Worker、质量样例、自动检查和部署配置。当前是个人内测首版，尚未完成真机故障验收、百炼多模态联调、生产 PostgreSQL / OSS 恢复演练和四周使用验证。

## 直接试用

安装包：`artifacts/SmartDiary-debug.apk`，要求 Android 8.0 或更新版本。Debug 包用于本机内测，应用 ID 为 `app.smartdiary.debug`，与未来正式包分开。

在当前电脑的项目目录运行：

```powershell
.\scripts\start-local.ps1 -UseDeepSeek
```

脚本使用已有 `.venv` 和继承的 `DEEPSEEK_API_KEY`，迁移数据库后同时启动 API 和 Worker。关闭脚本会停止 Worker。普通离线录入不需要注册或密钥。

手机 USB 连接并授权调试后运行：

```powershell
adb install -r artifacts/SmartDiary-debug.apk
adb reverse tcp:8000 tcp:8000
```

打开 App → 我的 → 账户与同步，服务地址填写 `http://127.0.0.1:8000`，选择创建账户，设置用户名和至少 10 位密码。开发模式默认不要求邀请码。安卓模拟器使用 `http://10.0.2.2:8000`。正式构建只允许 HTTPS。

可以先写一笔，再同步，在日记页选择日期并点整理，在记忆页搜索或提问。展开来源会回到原文和附件；原记录可以补充后续、标记结果、查看版本或改为仅本地。

DeepSeek 模式目前支持文字提取、日记、问答、伙伴和周回顾；语音原件与图片会可靠保存，但此模式不提供语音转写、图片识别或向量嵌入。完整多模态模式需要在服务端配置百炼；详情见 [配置与部署](docs/deployment.md)。

## 已实现的行为

| 能力 | 当前实现 |
| --- | --- |
| 随手录入 | 文字草稿、补记时间、链接、照片、系统分享、桌面组件与快捷入口 |
| 录音 | 暂停 / 继续 / 结束，30 秒分段，约每秒加密并刷盘，完整帧中断恢复、原音回放 |
| 本地保护 | Room 内容加密、Keystore 密钥、附件加密；预览只在内存解密；应用锁、隐藏通知正文 |
| 同步 | 本地先保存、UUID 幂等、游标增量、重试、冲突保留两份、编辑与上传并发保护 |
| 日记 | 当地 22:30 服务端生成，按发生时间归日；日记 / 时间线共用来源；人工段落保留、迟到补充待合并 |
| 回查 | 中文关键词、百炼向量混合检索、日期 / 人物 / 来源筛选；离线回退手机关键词搜索 |
| 记忆 | 人物别名、人工合并、待确认标记、主题与人物时间线、来源片段修正 |
| 连续经历 | 手工补充后续与决定结果，持久化关联记录；问答取证可扩展到相关结果 |
| AI 主动性 | 安静 / 适度 / 伙伴；每日上限、免打扰、最多两问、按需 TTS；周日回顾和相关旧记忆提示 |
| 数据掌控 | 删除与修正使派生数据失效；仅本地内容不上传；手机 / 云端 ZIP 导出恢复；独立删除账本 |
| 成本 | 调用前预留，失败保守计费，240 元预警、300 元上限，固定支出由配置估算 |

## 从零准备开发环境

需要 Python 3.11+（已验证 3.12）、JDK 17、Android SDK 35。首次 Gradle 下载需要网络；Android Studio 可直接打开 `android/`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e backend
Copy-Item .env.example .env
.\scripts\start-local.ps1
```

默认 `AI_MODE=disabled`，会展示可追溯的原文整理，明确区分 AI 生成。密钥留在服务端环境变量，不写入 APK 或仓库。

```powershell
.\scripts\build-android.ps1 -JdkPath '你的 JDK17 目录' -SdkPath '你的 Android SDK 目录'
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\ruff.exe check backend --config backend/pyproject.toml
.\.venv\Scripts\python.exe scripts/smoke.py
.\.venv\Scripts\python.exe backend/evaluation/run.py --live --output artifacts/deepseek-evaluation.json
```

最后一条会实际调用 DeepSeek，使用合成数据；其他命令不调用收费模型。连接干净的调试手机后，在 `android/` 执行 `gradlew.bat :app:connectedDebugAndroidTest`。设备测试会跳过已绑定账户的安装。

## 工程资料

- [产品范围与阶段门槛](docs/product.md)
- [架构、状态和数据一致性](docs/architecture.md)
- [接口契约](docs/api.md)，完整机器契约：[OpenAPI](docs/openapi.json)
- [配置、部署与恢复](docs/deployment.md)
- [测试记录与已知限制](docs/verification.md)
- [四周个人试用与验收表](docs/trial.md)

仓库没有附带真实日记、模型密钥或账户密码。`.data/`、`.env`、导出包和运行报告默认被 Git 忽略。导出 ZIP 包含可读内容，需要自行妥善保管；应用卸载后 Keystore 密钥会失效，迁移设备必须用导出包恢复。
