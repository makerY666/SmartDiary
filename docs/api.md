# v1 接口契约

完整字段见 `openapi.json`；运行 API 后可访问 `/docs`。除 `/health`、注册与登录外需要 `Authorization: Bearer <token>`。Token 有效七天，过期在 App 重新登录；同一安装绑定一个账户，避免混合本地日记。

| 范围 | 路径 |
| --- | --- |
| 账户 | POST `/v1/auth/register`、`/v1/auth/login` |
| 设置与预算 | GET / PUT `/v1/settings`，GET `/v1/budget`、`/v1/metrics` |
| 记录 | POST / GET `/v1/records`，GET / DELETE `/v1/records/{id}` |
| 记录历史 | GET `/v1/records/{id}/revisions`，POST `/v1/records/{id}/retry` |
| 连续经历 | GET `/v1/records/{id}/timeline`；创建后续时带 `parent_record_id` 与 `relation` |
| 附件 | POST `/v1/records/{id}/attachments/{attachment_id}`，GET `/v1/attachments/{attachment_id}` |
| 同步 | GET `/v1/sync?cursor=0` |
| 日记 | GET `/v1/diaries`，POST `/v1/diaries/{day}/generate`，PUT `/v1/diaries/{day}`，POST `/v1/diaries/{day}/merge?version=...` |
| 回查 | POST `/v1/search`、`/v1/ask` |
| 记忆 | GET `/v1/memories`，PUT `/v1/memories/{id}` |
| 人物与主题 | GET `/v1/people`、`/v1/timelines?person=...&topic=...`，PUT `/v1/people/{id}`，POST `/v1/people/{id}/merge` |
| 伙伴 | GET `/v1/messages`，POST `/v1/chat`、`/v1/reviews/weekly` |
| 提醒 | GET / POST `/v1/reminders`，POST `/v1/reminders/{id}/complete` |
| 数据掌控 | GET `/v1/export`，POST multipart `/v1/restore` |

## 记录示例

```json
{
  "id": "dbcf83b6-a965-4c9e-8b9d-98c2696d9d48",
  "kind": "text",
  "text": "决定周末坐高铁去杭州，因为不想疲劳驾驶。",
  "occurred_at": "2026-10-02T18:00:00+08:00",
  "recorded_at": "2026-10-02T18:01:00+08:00",
  "source_type": "personal",
  "base_version": 0
}
```

`kind`: text / audio / image / link；`source_type`: personal / external。时间必须有时区。UUID 由客户端生成。相同创建重试不会创建第二份；修改使用服务器最新版本。后续记录填写 `parent_record_id`，决定结果使用 `relation=result`。关联必须属于当前用户，不能关联自身。

仅本地是客户端策略，不存在可接受敏感原文的云端“本地”字段：这些记录完全不上传。切换已同步内容会调用删除接口，并在手机保留副本。

## 来源格式

```json
{
  "record_id": "dbcf83b6-a965-4c9e-8b9d-98c2696d9d48",
  "version": 1,
  "start": 0,
  "end": 10,
  "quote": "决定周末坐高铁去杭州",
  "occurred_at": "2026-10-02T10:00:00+00:00",
  "source_type": "personal",
  "attachment_ids": []
}
```

`start/end` 为 Python Unicode 码点索引，`end` 不包含末字符；客户端通常直接使用 `quote`，不要按 UTF-16 索引重新截取。日记、时间线、问答和伙伴返回同一来源约定。

问答返回 `answer`、`sources`、`uncertain` 和 `mode`。`source_excerpts` 是原文回退，`no_evidence` 是未找到足够依据，`stale` 表示调用期间来源已修改，`ai` 表示模型生成且引用结构验证通过。引用验证不能代替人工事实审查。

## 同步、冲突与失败

变化序列返回当前快照，避免旧同步游标重新暴露已删除正文。消费完一批再持久化游标；`has_more=true` 时继续拉取。记录版本冲突返回 409 和服务器快照，客户端保留本地文字；用户选择版本后基于最新版本提交。

401 重新登录；404 不存在或不可访问；409 版本冲突；410 已删除 UUID；415 附件格式不支持；422 字段无效；429 预算不足；503 模型不可用。后台失败任务保留原始内容，可手动重试。

## 导出恢复

云端 ZIP 包含 Markdown 日记、JSON 记录 / 来源 / 所有日记版本 / 修正版本 / 人物 / 消息 / 提醒 / 删除 UUID，以及原始附件；不导出密码、JWT、模型密钥或向量。

附件上传表单支持 `position`（捕获顺序，从 0 开始）和 `preserve_text`（手机恢复已接受的文字时为 true，保留附件但跳过再次识别）。新拍照或新录音默认 false。云端导出保留识别缓存；已处理记录恢复后只重建向量，不改写文字和人工修正。

云端恢复限定当前账户；已存在或已删除 UUID 不覆盖、不复活。先校验所有字段、附件格式 / 哈希和无循环关联，再按原记录依赖顺序导入。导入中数据库或存储故障可重试，已完成 UUID 会跳过。上限为 100MB 解压内容、单附件 20MB、5000 条 ZIP 条目；长期大规模导出分片能力仍待扩展。

手机 ZIP 另用 `smartdiary-local-v1` 格式，包含本地私密内容、草稿、版本、缓存与附件。两个格式使用 App 中各自的恢复入口。手机恢复仍遵守本地删除标记，不覆盖已有记录；手机备份必须包含附件，缺失云端附件时需联网下载后导出。
