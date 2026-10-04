import hashlib
from contextlib import asynccontextmanager
from datetime import date, datetime
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import case, func, select

from . import storage
from .ai import AIUnavailable
from .auth import current_user, issue_token, register, verify_password
from .budget import BudgetExceeded, overview
from .companion import respond, weekly_review
from .companion import wire as message_wire
from .config import settings
from .db import get_db
from .domain import (
    aware,
    changed,
    diary_wire,
    enqueue,
    erase_record,
    generate_diary,
    latest_diary,
    owned_record,
    record_wire,
    source,
    write_record,
)
from .exporting import export_bundle, restore_bundle
from .models import (
    Attachment,
    Change,
    Diary,
    Job,
    Memory,
    Message,
    Person,
    Record,
    RecordLink,
    RecordRevision,
    Reminder,
    Usage,
    User,
    uid,
)
from .retrieval import answer, search
from .schemas import (
    ChatWrite,
    Credentials,
    DiaryEdit,
    MemoryEdit,
    PersonEdit,
    PersonMerge,
    Preferences,
    Query,
    RecordWrite,
    ReminderWrite,
)


@asynccontextmanager
async def lifespan(_):
    settings.validate_production()
    yield


app = FastAPI(title="SmartDiary", version="0.1.0", lifespan=lifespan)


@app.exception_handler(AIUnavailable)
async def ai_error(_, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(BudgetExceeded)
async def budget_error(_, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/v1/auth/register")
def signup(body: Credentials, db=Depends(get_db)):
    user = register(db, body)
    return {"token": issue_token(user), "user_id": user.id, "username": user.username}


@app.post("/v1/auth/login")
def login(body: Credentials, db=Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": issue_token(user), "user_id": user.id, "username": user.username}


@app.get("/v1/settings")
def get_settings(user=Depends(current_user)):
    return Preferences(**user.settings)


@app.put("/v1/settings")
def put_settings(body: Preferences, user=Depends(current_user), db=Depends(get_db)):
    user.settings = body.model_dump()
    changed(db, user.id, "settings", user.id)
    db.commit()
    return body


@app.get("/v1/budget")
def get_budget(user=Depends(current_user), db=Depends(get_db)):
    return overview(db)


@app.get("/v1/metrics")
def metrics(user=Depends(current_user), db=Depends(get_db)):
    operations = db.execute(
        select(
            Usage.operation,
            func.count(Usage.id),
            func.sum(case((Usage.state == "failed", 1), else_=0)),
            func.avg(Usage.latency_ms),
            func.sum(Usage.spent_yuan),
        )
        .where(Usage.user_id == user.id)
        .group_by(Usage.operation)
    )
    return {
        "operations": [
            {
                "operation": op,
                "calls": count,
                "failures": failed,
                "mean_latency_ms": round(float(latency or 0), 1),
                "estimated_yuan": round(float(cost or 0), 4),
            }
            for op, count, failed, latency, cost in operations
        ],
        "tasks": {
            state: count
            for state, count in db.execute(
                select(Job.state, func.count(Job.id)).where(Job.user_id == user.id).group_by(Job.state)
            )
        },
    }


@app.post("/v1/records")
def create_record(body: RecordWrite, user=Depends(current_user), db=Depends(get_db)):
    return write_record(db, user, body)


@app.get("/v1/records")
def list_records(user=Depends(current_user), db=Depends(get_db), limit: int = 100, before: str | None = None):
    stmt = select(Record).where(Record.user_id == user.id, Record.deleted.is_(False))
    if before:
        try:
            stmt = stmt.where(Record.recorded_at < datetime.fromisoformat(before))
        except ValueError:
            raise HTTPException(422, "before 必须是 ISO 时间") from None
    return [
        record_wire(r, db)
        for r in db.scalars(stmt.order_by(Record.recorded_at.desc()).limit(max(1, min(500, limit))))
    ]


@app.get("/v1/records/{rid}")
def get_record(rid: UUID, user=Depends(current_user), db=Depends(get_db)):
    return record_wire(owned_record(db, user.id, rid), db)


@app.get("/v1/records/{rid}/timeline")
def record_timeline(rid: UUID, user=Depends(current_user), db=Depends(get_db)):
    owned_record(db, user.id, rid)
    links = list(db.scalars(select(RecordLink).where(RecordLink.user_id == user.id)))
    ids = {str(rid)}
    for _ in range(100):
        related = {link.child_id for link in links if link.parent_id in ids} | {
            link.parent_id for link in links if link.child_id in ids
        }
        if related <= ids or len(ids) >= 100:
            break
        ids.update(related)
    items = []
    for record in db.scalars(
        select(Record)
        .where(Record.user_id == user.id, Record.id.in_(ids), Record.deleted.is_(False))
        .order_by(Record.occurred_at)
    ):
        link = next((candidate for candidate in links if candidate.child_id == record.id), None)
        items.append(
            {
                "id": record.id,
                "title": "后续结果" if link and link.relation == "result" else "相关记录",
                "text": record.text,
                "kind": link.relation if link else "event",
                "sources": [source(db, record)],
            }
        )
    return {"title": "这件事的连续记录", "items": items}


@app.delete("/v1/records/{rid}")
def delete_record(rid: UUID, user=Depends(current_user), db=Depends(get_db)):
    erase_record(db, user, str(rid))
    return {"deleted": True, "record_id": str(rid)}


@app.get("/v1/records/{rid}/revisions")
def revisions(rid: UUID, user=Depends(current_user), db=Depends(get_db)):
    owned_record(db, user.id, rid)
    return [
        {"version": r.version, "kind": r.kind, "snapshot": r.snapshot}
        for r in db.scalars(
            select(RecordRevision).where(
                RecordRevision.user_id == user.id, RecordRevision.record_id == str(rid)
            )
        )
    ]


@app.post("/v1/records/{rid}/retry")
def retry(rid: UUID, user=Depends(current_user), db=Depends(get_db)):
    record = owned_record(db, user.id, rid)
    enqueue(
        db, user.id, "process", f"retry:{rid}:{uid()}", {"record_id": str(rid), "version": record.version}
    )
    record.status, record.error = "pending", ""
    changed(db, user.id, "record", record.id)
    db.commit()
    return record_wire(record, db)


@app.post("/v1/records/{rid}/attachments/{aid}")
async def upload(
    rid: UUID,
    aid: UUID,
    file: UploadFile = File(...),
    duration_seconds: float = Form(0),
    position: int = Form(0, ge=0, le=100000),
    preserve_text: bool = Form(False),
    user=Depends(current_user),
    db=Depends(get_db),
):
    record = owned_record(db, user.id, rid)
    data = await file.read(settings.max_attachment_bytes + 1)
    if not data or len(data) > settings.max_attachment_bytes or not 0 <= duration_seconds <= 300:
        raise HTTPException(413, "附件上限 20MB，单段录音上限 5 分钟")
    if not storage.valid_media(data, file.content_type or ""):
        raise HTTPException(415, "不支持或损坏的图片/音频格式")
    existing = db.get(Attachment, str(aid))
    digest = hashlib.sha256(data).hexdigest()
    if existing:
        if existing.user_id != user.id or existing.record_id != record.id:
            raise HTTPException(404, "附件不存在")
        if existing.sha256 != digest:
            raise HTTPException(409, "附件 UUID 已对应其他内容")
        return {"id": existing.id, "sha256": digest}
    key, digest = storage.put(user.id, str(aid), data)
    db.add(
        Attachment(
            id=str(aid),
            record_id=record.id,
            user_id=user.id,
            mime_type=file.content_type,
            storage_key=key,
            sha256=digest,
            size=len(data),
            duration_seconds=duration_seconds,
            position=position,
            preserve_text=preserve_text,
        )
    )
    record.status = "pending"
    enqueue(db, user.id, "process", f"attachment:{aid}", {"record_id": record.id, "version": record.version})
    changed(db, user.id, "record", record.id)
    db.commit()
    return {"id": str(aid), "sha256": digest}


@app.get("/v1/attachments/{aid}")
def download(aid: UUID, user=Depends(current_user), db=Depends(get_db)):
    item = db.scalar(select(Attachment).where(Attachment.id == str(aid), Attachment.user_id == user.id))
    if not item:
        raise HTTPException(404, "附件不存在")
    owned_record(db, user.id, item.record_id)
    return Response(
        storage.read(item.storage_key),
        media_type=item.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/v1/sync")
def sync(cursor: int = 0, user=Depends(current_user), db=Depends(get_db)):
    changes = list(
        db.scalars(
            select(Change)
            .where(Change.user_id == user.id, Change.seq > max(0, cursor))
            .order_by(Change.seq)
            .limit(200)
        )
    )
    output = []
    for change in changes:
        payload = None
        if change.kind == "record":
            record = db.get(Record, change.entity_id)
            if record and record.user_id == user.id:
                payload = record_wire(record, db)
        elif change.kind == "diary":
            diary = db.get(Diary, change.entity_id)
            if diary and diary.user_id == user.id:
                payload = diary_wire(diary)
        elif change.kind == "message":
            message = db.get(Message, change.entity_id)
            if message and message.user_id == user.id:
                payload = message_wire(message)
        elif change.kind == "settings":
            payload = Preferences(**user.settings).model_dump()
        if payload is not None:
            output.append({"seq": change.seq, "kind": change.kind, "payload": payload})
    return {
        "cursor": changes[-1].seq if changes else cursor,
        "has_more": len(changes) == 200,
        "changes": output,
    }


@app.get("/v1/diaries")
def list_diaries(user=Depends(current_user), db=Depends(get_db)):
    days = list(
        db.scalars(select(Diary.day).where(Diary.user_id == user.id).distinct().order_by(Diary.day.desc()))
    )
    return [diary_wire(latest_diary(db, user.id, day)) for day in days]


@app.post("/v1/diaries/{day}/generate")
def make_diary(day: date, user=Depends(current_user), db=Depends(get_db)):
    diary = generate_diary(db, user, day.isoformat())
    return (
        diary_wire(diary)
        if diary
        else {"day": day.isoformat(), "paragraphs": [], "timeline": [], "pending": [], "version": 0}
    )


@app.put("/v1/diaries/{day}")
def edit_diary(day: date, body: DiaryEdit, user=Depends(current_user), db=Depends(get_db)):
    previous = latest_diary(db, user.id, day.isoformat())
    if not previous:
        raise HTTPException(404, "日记不存在")
    if previous.version != body.base_version:
        raise HTTPException(409, {"message": "日记已更新，请合并修改", "server": diary_wire(previous)})
    if not any(p["id"] == body.paragraph_id for p in previous.paragraphs):
        raise HTTPException(404, "段落不存在")
    paragraphs = [
        {**p, "text": body.text, "edited": True} if p["id"] == body.paragraph_id else p
        for p in previous.paragraphs
    ]
    diary = Diary(
        user_id=user.id,
        day=previous.day,
        version=previous.version + 1,
        paragraphs=paragraphs,
        timeline=previous.timeline,
        pending=previous.pending,
    )
    db.add(diary)
    db.flush()
    changed(db, user.id, "diary", diary.id)
    db.commit()
    return diary_wire(diary)


@app.post("/v1/diaries/{day}/merge")
def merge_diary(day: date, version: int, user=Depends(current_user), db=Depends(get_db)):
    previous = latest_diary(db, user.id, day.isoformat())
    if not previous or previous.version != version:
        raise HTTPException(409, "日记已更新，请刷新后合并")
    diary = Diary(
        user_id=user.id,
        day=previous.day,
        version=previous.version + 1,
        paragraphs=previous.paragraphs + previous.pending,
        timeline=previous.timeline,
        pending=[],
    )
    db.add(diary)
    db.flush()
    changed(db, user.id, "diary", diary.id)
    db.commit()
    return diary_wire(diary)


def check_query(body):
    try:
        for value in (body.day_from, body.day_to):
            if value:
                date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from None


@app.post("/v1/search")
def find(body: Query, user=Depends(current_user), db=Depends(get_db)):
    check_query(body)
    return {"results": search(db, user, body)}


@app.post("/v1/ask")
def ask(body: Query, user=Depends(current_user), db=Depends(get_db)):
    check_query(body)
    return answer(db, user, body)


@app.get("/v1/memories")
def memories(user=Depends(current_user), db=Depends(get_db)):
    output = []
    for memory in db.scalars(select(Memory).where(Memory.user_id == user.id)):
        record = db.get(Record, memory.record_id)
        if (
            record
            and not record.deleted
            and not record.superseded_by
            and memory.record_version == record.version
        ):
            output.append(
                {
                    "id": memory.id,
                    "title": memory.title,
                    "detail": memory.detail,
                    "kind": memory.kind,
                    "people": memory.people,
                    "topics": memory.topics,
                    "confirmed": memory.confirmed,
                    "source": source(db, record, record.text[memory.source_start : memory.source_end]),
                }
            )
    return output


@app.put("/v1/memories/{mid}")
def correct_memory(mid: UUID, body: MemoryEdit, user=Depends(current_user), db=Depends(get_db)):
    memory = db.scalar(select(Memory).where(Memory.id == str(mid), Memory.user_id == user.id))
    if not memory:
        raise HTTPException(404, "记忆不存在")
    record = owned_record(db, user.id, memory.record_id)
    occurred = aware(record.occurred_at)
    old_kind = memory.kind
    start, end = memory.source_start, memory.source_end
    corrected_text = record.text[:start] + body.detail + record.text[end:]
    payload = RecordWrite(
        id=record.id,
        kind=record.kind,
        text=corrected_text,
        url=record.url,
        base_version=record.version,
        occurred_at=occurred,
        recorded_at=aware(record.recorded_at),
        source_type=record.source_type,
    )
    write_record(db, user, payload, force_revision=True)
    correction = db.get(Record, str(payload.id))
    corrected = Memory(
        user_id=user.id,
        record_id=correction.id,
        record_version=correction.version,
        title=body.title,
        detail=body.detail,
        people=body.people,
        topics=body.topics,
        confirmed=True,
        kind=old_kind,
        source_start=start,
        source_end=start + len(body.detail),
    )
    db.add(corrected)
    db.commit()
    return {"id": corrected.id, "correction_record_id": correction.id}


@app.get("/v1/people")
def people(user=Depends(current_user), db=Depends(get_db)):
    return [
        {"id": p.id, "name": p.name, "aliases": p.aliases, "confirmed": p.confirmed}
        for p in db.scalars(select(Person).where(Person.user_id == user.id))
    ]


@app.get("/v1/timelines")
def timeline(person: str = "", topic: str = "", user=Depends(current_user), db=Depends(get_db)):
    aliases = {person} if person else set()
    for p in db.scalars(select(Person).where(Person.user_id == user.id)):
        if person in [p.id, p.name] + p.aliases:
            aliases.update([p.name] + p.aliases)
    output = []
    for memory in db.scalars(select(Memory).where(Memory.user_id == user.id)):
        record = db.get(Record, memory.record_id)
        if not record or record.deleted or record.superseded_by or record.version != memory.record_version:
            continue
        if person and not any(name in memory.people or name in record.text for name in aliases):
            continue
        if topic and topic not in memory.topics and topic not in memory.title and topic not in record.text:
            continue
        output.append(
            {
                "id": memory.id,
                "title": memory.title,
                "text": memory.detail,
                "kind": memory.kind,
                "sources": [source(db, record)],
            }
        )
    output.sort(key=lambda item: item["sources"][0]["occurred_at"])
    return {"title": person or topic or "经历时间线", "items": output}


@app.put("/v1/people/{pid}")
def edit_person(pid: UUID, body: PersonEdit, user=Depends(current_user), db=Depends(get_db)):
    person = db.scalar(select(Person).where(Person.id == str(pid), Person.user_id == user.id))
    if not person:
        raise HTTPException(404, "人物不存在")
    old = person.name
    person.name, person.aliases, person.confirmed = body.name, list(dict.fromkeys(body.aliases + [old])), True
    db.commit()
    return {"id": person.id, "name": person.name, "aliases": person.aliases, "confirmed": True}


@app.post("/v1/people/{pid}/merge")
def merge_people(pid: UUID, body: PersonMerge, user=Depends(current_user), db=Depends(get_db)):
    person = db.scalar(select(Person).where(Person.id == str(pid), Person.user_id == user.id))
    if not person:
        raise HTTPException(404, "人物不存在")
    aliases = set(person.aliases)
    for sid in body.source_ids:
        candidate = db.scalar(select(Person).where(Person.id == str(sid), Person.user_id == user.id))
        if not candidate:
            raise HTTPException(404, "人物不存在")
        if candidate.id != person.id:
            aliases.update([candidate.name] + candidate.aliases)
            db.delete(candidate)
    person.aliases, person.confirmed = sorted(aliases), True
    db.commit()
    return {"id": person.id, "aliases": person.aliases}


@app.get("/v1/messages")
def messages(user=Depends(current_user), db=Depends(get_db)):
    return [
        message_wire(m)
        for m in db.scalars(
            select(Message).where(Message.user_id == user.id).order_by(Message.created_at.desc()).limit(100)
        )
    ][::-1]


@app.post("/v1/chat")
def chat(body: ChatWrite, user=Depends(current_user), db=Depends(get_db)):
    refs = []
    if body.record_id:
        record = owned_record(db, user.id, body.record_id)
        if record.superseded_by:
            raise HTTPException(409, "记录已修正，请使用新的来源")
        refs = [source(db, record)]
    message = Message(user_id=user.id, role="user", text=body.question, sources=refs)
    db.add(message)
    db.flush()
    changed(db, user.id, "message", message.id)
    db.commit()
    reply = respond(db, user, body.question, record_id=str(body.record_id) if body.record_id else None)
    return message_wire(reply) if reply else {"text": "相关内容已更新，请重试", "sources": []}


@app.post("/v1/reviews/weekly")
def review(user=Depends(current_user), db=Depends(get_db)):
    return weekly_review(db, user)


@app.get("/v1/reminders")
def reminders(user=Depends(current_user), db=Depends(get_db)):
    return [
        {
            "id": r.id,
            "text": r.text,
            "due_at": aware(r.due_at).isoformat(),
            "confirmed": r.confirmed,
            "completed": r.completed,
        }
        for r in db.scalars(select(Reminder).where(Reminder.user_id == user.id).order_by(Reminder.due_at))
    ]


@app.post("/v1/reminders")
def create_reminder(body: ReminderWrite, user=Depends(current_user), db=Depends(get_db)):
    if not body.confirmed:
        raise HTTPException(422, "请先确认提醒内容与时间")
    from datetime import timezone

    reminder = Reminder(
        user_id=user.id, text=body.text, confirmed=True, due_at=body.due_at.astimezone(timezone.utc)
    )
    db.add(reminder)
    db.commit()
    return {
        "id": reminder.id,
        "text": reminder.text,
        "due_at": aware(reminder.due_at).isoformat(),
        "confirmed": True,
    }


@app.post("/v1/reminders/{rid}/complete")
def complete_reminder(rid: UUID, user=Depends(current_user), db=Depends(get_db)):
    reminder = db.scalar(select(Reminder).where(Reminder.id == str(rid), Reminder.user_id == user.id))
    if not reminder:
        raise HTTPException(404, "提醒不存在")
    reminder.completed = True
    db.commit()
    return {"completed": True}


@app.get("/v1/export")
def export(user=Depends(current_user), db=Depends(get_db)):
    return Response(
        export_bundle(db, user),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="smartdiary-export.zip"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/v1/restore")
async def restore(file: UploadFile = File(...), user=Depends(current_user), db=Depends(get_db)):
    data = await file.read(100 * 1024 * 1024 + 1)
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "恢复包上限 100MB")
    return restore_bundle(db, user, data)
