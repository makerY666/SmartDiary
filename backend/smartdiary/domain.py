from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, select

from . import storage
from .ai import Bailian
from .config import settings
from .db import utcnow
from .locking import serialized
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
    Tombstone,
    uid,
)
from .schemas import Preferences


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def preferences(user):
    return Preferences(**user.settings)


def record_wire(record, db=None):
    result = {
        "id": record.id,
        "kind": record.kind,
        "text": record.text,
        "original_text": record.original_text,
        "url": record.url,
        "occurred_at": aware(record.occurred_at).isoformat(),
        "recorded_at": aware(record.recorded_at).isoformat(),
        "version": record.version,
        "source_type": record.source_type,
        "deleted": record.deleted,
        "superseded_by": record.superseded_by,
        "status": record.status,
        "error": record.error,
    }
    if db:
        link = db.scalar(
            select(RecordLink).where(RecordLink.user_id == record.user_id, RecordLink.child_id == record.id)
        )
        result["parent_record_id"] = link.parent_id if link else None
        result["relation"] = link.relation if link else "followup"
        result["attachments"] = [
            {
                "id": a.id,
                "mime_type": a.mime_type,
                "duration_seconds": a.duration_seconds,
                "preserve_text": a.preserve_text,
            }
            for a in db.scalars(
                select(Attachment)
                .where(Attachment.record_id == record.id, Attachment.user_id == record.user_id)
                .order_by(Attachment.position, Attachment.id)
            )
        ]
    return result


def changed(db, user_id, kind, entity_id):
    db.add(Change(user_id=user_id, kind=kind, entity_id=entity_id, payload={}))


def enqueue(db, user_id, kind, key, payload):
    existing = db.scalar(select(Job).where(Job.dedupe_key == key))
    if not existing:
        db.add(Job(user_id=user_id, kind=kind, dedupe_key=key, payload=payload))


def owned_record(db, user_id, record_id, include_deleted=False):
    record = db.scalar(select(Record).where(Record.id == str(record_id), Record.user_id == user_id))
    if not record or (record.deleted and not include_deleted):
        raise HTTPException(404, "记录不存在")
    return record


def source(db, record, quote=None):
    quote = quote or record.text
    start = record.text.find(quote)
    if start < 0:
        raise ValueError("source quote is not present in record")
    return {
        "record_id": record.id,
        "version": record.version,
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "occurred_at": aware(record.occurred_at).isoformat(),
        "source_type": record.source_type,
        "attachment_ids": list(
            db.scalars(
                select(Attachment.id).where(
                    Attachment.record_id == record.id, Attachment.user_id == record.user_id
                )
            )
        ),
    }


def invalidate(db, record, erase=False):
    db.execute(delete(Memory).where(Memory.record_id == record.id, Memory.user_id == record.user_id))
    for diary in db.scalars(select(Diary).where(Diary.user_id == record.user_id)):

        def keep(items):
            output = []
            for item in items:
                ids = [s.get("record_id") for s in item.get("sources", [])] + item.get(
                    "pending_source_ids", []
                )
                if record.id not in ids:
                    output.append(item)
                elif item.get("edited") and not erase:
                    output.append(
                        {
                            **item,
                            "stale": True,
                            "sources": [
                                s for s in item.get("sources", []) if s.get("record_id") != record.id
                            ],
                            "pending_source_ids": list(
                                dict.fromkeys(item.get("pending_source_ids", []) + [record.id])
                            ),
                        }
                    )
            return output

        original = (diary.paragraphs, diary.timeline, diary.pending)
        diary.paragraphs, diary.timeline, diary.pending = (
            keep(diary.paragraphs),
            keep(diary.timeline),
            keep(diary.pending),
        )
        if original != (diary.paragraphs, diary.timeline, diary.pending):
            changed(db, record.user_id, "diary", diary.id)
    for message in db.scalars(select(Message).where(Message.user_id == record.user_id)):
        if any(s.get("record_id") == record.id for s in message.sources):
            message.text, message.sources = "这条回应的来源已修改或删除，请重新查询。", []
            changed(db, record.user_id, "message", message.id)
    if erase:
        db.execute(
            delete(RecordRevision).where(
                RecordRevision.record_id == record.id, RecordRevision.user_id == record.user_id
            )
        )
        for job in db.scalars(select(Job).where(Job.user_id == record.user_id)):
            if job.payload.get("record_id") == record.id:
                job.state, job.payload = "cancelled", {}
        # Drop orphan, unconfirmed names extracted from the erased source.
        remaining = list(db.scalars(select(Memory).where(Memory.user_id == record.user_id)))
        names = {name for memory in remaining for name in memory.people}
        for person in db.scalars(
            select(Person).where(Person.user_id == record.user_id, Person.confirmed.is_(False))
        ):
            if person.name not in names:
                db.delete(person)


def write_record(db, user, payload, *, schedule_processing=True, commit=True, force_revision=False):
    rid = str(payload.id)
    if payload.parent_record_id:
        if str(payload.parent_record_id) == rid:
            raise HTTPException(422, "后续记录不能关联自身")
        owned_record(db, user.id, payload.parent_record_id)
    if storage.was_deleted(user.id, rid) or db.scalar(
        select(Tombstone).where(Tombstone.user_id == user.id, Tombstone.record_id == rid)
    ):
        raise HTTPException(410, "该记录已从云端删除，不可自动重新上传")
    record = db.scalar(select(Record).where(Record.id == rid).with_for_update())
    if record and record.user_id != user.id:
        raise HTTPException(404, "记录不存在")
    if record and record.deleted:
        raise HTTPException(410, "记录已删除")
    identical = (
        record
        and record.kind == payload.kind
        and record.url == payload.url
        and (
            record.text == payload.text
            or (payload.base_version == 0 and record.original_text == payload.text)
        )
        and aware(record.occurred_at) == payload.occurred_at
        and record.source_type == payload.source_type
    )
    if identical and not force_revision:
        return record_wire(record, db)
    if record and payload.base_version != record.version:
        db.add(
            RecordRevision(
                user_id=user.id,
                record_id=rid,
                version=payload.base_version,
                kind="conflict",
                snapshot=payload.model_dump(mode="json"),
            )
        )
        db.commit()
        raise HTTPException(
            409, {"message": "检测到另一版本，两份内容已保留", "server": record_wire(record, db)}
        )
    if not record and payload.base_version != 0:
        raise HTTPException(409, "新记录的 base_version 必须为 0")
    if record:
        db.add(
            RecordRevision(
                user_id=user.id, record_id=rid, version=record.version, snapshot=record_wire(record, db)
            )
        )
        invalidate(db, record)
        old_day = aware(record.occurred_at).astimezone(ZoneInfo(preferences(user).timezone)).date()
        enqueue(db, user.id, "diary", f"diary-change:{rid}:{record.version}:{old_day}", {"day": str(old_day)})
        record.version += 1
    else:
        record = Record(id=rid, user_id=user.id, original_text=payload.text, version=1)
        db.add(record)
    record.kind, record.text, record.url = payload.kind, payload.text, payload.url
    record.occurred_at, record.recorded_at = (
        payload.occurred_at.astimezone(timezone.utc),
        payload.recorded_at.astimezone(timezone.utc),
    )
    record.source_type, record.status, record.error = payload.source_type, "pending", ""
    record.updated_at = utcnow()
    db.flush()
    if payload.parent_record_id and not db.scalar(
        select(RecordLink).where(RecordLink.user_id == user.id, RecordLink.child_id == rid)
    ):
        db.add(
            RecordLink(
                user_id=user.id,
                parent_id=str(payload.parent_record_id),
                child_id=rid,
                relation=payload.relation,
            )
        )
    changed(db, user.id, "record", rid)
    if schedule_processing:
        enqueue(
            db,
            user.id,
            "process",
            f"process:{rid}:{record.version}",
            {"record_id": rid, "version": record.version},
        )
    if commit:
        db.commit()
    return record_wire(record, db)


def erase_record(db, user, rid, *, commit=True):
    record = owned_record(db, user.id, rid, True)
    if record.deleted:
        return
    storage.mark_deleted(user.id, record.id)
    invalidate(db, record, erase=True)
    from sqlalchemy import or_

    db.execute(
        delete(RecordLink).where(
            RecordLink.user_id == user.id, or_(RecordLink.parent_id == rid, RecordLink.child_id == rid)
        )
    )
    for attachment in db.scalars(
        select(Attachment).where(Attachment.record_id == record.id, Attachment.user_id == user.id)
    ):
        # Delete encrypted media before completing the deletion receipt; failure is retryable.
        storage.remove(attachment.storage_key)
        db.delete(attachment)
    record.text = record.original_text = record.url = record.error = ""
    record.deleted, record.status, record.version = True, "deleted", record.version + 1
    record.source_type = "personal"
    db.add(Tombstone(user_id=user.id, record_id=record.id))
    changed(db, user.id, "record", record.id)
    if commit:
        db.commit()


@serialized(lambda db, user, rid, expected_version: f"process:{user.id}:{rid}")
def process_record(db, user, rid, expected_version):
    record = db.get(Record, rid)
    if (
        not record
        or record.deleted
        or record.superseded_by
        or record.user_id != user.id
        or record.version != expected_version
    ):
        return
    ai = Bailian(db, user.id)
    link = db.scalar(select(RecordLink).where(RecordLink.user_id == user.id, RecordLink.child_id == rid))
    text = record.text
    attachments = list(
        db.scalars(
            select(Attachment)
            .where(Attachment.record_id == rid, Attachment.user_id == user.id)
            .order_by(Attachment.position, Attachment.id)
        )
    )
    if not text and record.kind in {"audio", "image"} and not attachments:
        # Uploading the record precedes its attachments. Upload enqueues another job.
        return
    if settings.ai_mode == "bailian":
        media_texts = []
        new_media_texts = []
        for item in attachments:
            if item.preserve_text:
                continue
            if item.extracted_text:
                media_texts.append(item.extracted_text)
                continue
            data = storage.read(item.storage_key)
            if item.mime_type.startswith("audio/"):
                item.extracted_text = ai.transcribe(data, item.mime_type, item.duration_seconds)
            elif item.mime_type.startswith("image/"):
                item.extracted_text = ai.image(data, item.mime_type)
            media_texts.append(item.extracted_text)
            new_media_texts.append(item.extracted_text)
        if media_texts and record.version == 1:
            text = record.original_text + "\n" + "\n".join(media_texts)
        elif new_media_texts:
            text += "\n" + "\n".join(new_media_texts)
    elif attachments and not text:
        record.status, record.error = "needs_ai", "原始附件已保存，配置云端 AI 后可转写或提取文字"
        changed(db, user.id, "record", rid)
        db.commit()
        return
    if record.kind == "link" and record.url and settings.allow_public_web_fetch:
        from .web_fetch import extract

        try:
            body = extract(record.url)
            if body not in text:
                text += "\n" + body
        except (ValueError, OSError):
            pass  # Keep the user's shared content even when extraction is unavailable.
    # Provider calls commit budget entries. Reload to reject a deletion/update during the call.
    db.refresh(record)
    if record.deleted or record.version != expected_version:
        return
    record.text = text.strip()
    db.flush()
    if not record.text:
        record.status = "ready_basic"
        changed(db, user.id, "record", rid)
        db.commit()
        return
    events = [
        {
            "title": record.text[:30],
            "detail": record.text,
            "kind": "knowledge" if record.source_type == "external" else "event",
            "quote": record.text,
            "people": [],
            "topics": [],
        }
    ]
    if settings.text_ai_enabled:
        result = ai.json(
            "提取最多8个事件，返回 events 数组。每项包括 title, detail, kind(event/knowledge/decision/result),"
            "quote(原文连续片段), people(原文明确姓名), topics。detail 不增加原文没有的信息。",
            {"record_id": rid, "source_type": record.source_type, "text": record.text},
        )
        events = result.get("events", [])[:8]
    db.refresh(record)
    if record.deleted or record.version != expected_version:
        return
    db.execute(delete(Memory).where(Memory.record_id == rid, Memory.confirmed.is_(False)))
    memories = []
    for event in events:
        quote = str(event.get("quote", ""))
        if not quote or quote not in record.text:
            continue
        people = [str(p)[:120] for p in event.get("people", []) if isinstance(p, str) and p in quote][:20]
        kind = event.get("kind", "event")
        if kind not in {"event", "knowledge", "decision", "result"}:
            kind = "event"
        if record.source_type == "external":
            kind = "knowledge"
        elif link and link.relation == "result":
            kind = "result"
        memory = Memory(
            user_id=user.id,
            record_id=rid,
            record_version=record.version,
            title=str(event.get("title", quote[:30]))[:300],
            detail=str(event.get("detail", quote))[:20000],
            kind=kind,
            people=people,
            topics=[str(t)[:120] for t in event.get("topics", [])][:20],
            source_start=record.text.find(quote),
            source_end=record.text.find(quote) + len(quote),
        )
        db.add(memory)
        memories.append(memory)
        for name in people:
            if not db.scalar(select(Person).where(Person.user_id == user.id, Person.name == name)):
                db.add(Person(user_id=user.id, name=name))
                db.flush()
    if not memories:
        memories = [
            Memory(
                user_id=user.id,
                record_id=rid,
                record_version=record.version,
                title=record.text[:30],
                detail=record.text,
                people=[],
                topics=[],
                kind="knowledge" if record.source_type == "external" else "event",
                source_end=len(record.text),
            )
        ]
        db.add(memories[0])
    db.flush()
    vectors = ai.embeddings([m.detail[:6000] for m in memories])
    db.refresh(record)
    if record.deleted or record.version != expected_version:
        db.rollback()
        return
    for memory, vector in zip(memories, vectors):
        memory.embedding, memory.vector = vector, vector
    record.status, record.error = ("ready" if settings.text_ai_enabled else "ready_basic"), ""
    changed(db, user.id, "record", rid)
    day = aware(record.occurred_at).astimezone(ZoneInfo(preferences(user).timezone)).date()
    local_now = utcnow().astimezone(ZoneInfo(preferences(user).timezone))
    if (
        latest_diary(db, user.id, str(day))
        or day < local_now.date()
        or local_now.strftime("%H:%M") >= preferences(user).diary_time
    ):
        enqueue(db, user.id, "diary", f"diary:{rid}:{record.version}:{len(attachments)}", {"day": str(day)})
    if preferences(user).proactivity == "companion":
        enqueue(db, user.id, "companion", f"companion:{rid}:{record.version}", {"record_id": rid})
    db.commit()


@serialized(lambda db, user, rid, expected_version: f"process:{user.id}:{rid}")
def reindex_record(db, user, rid, expected_version):
    """Restore vectors without transcribing or rewriting an accepted snapshot."""
    record = db.get(Record, rid)
    if not record or record.user_id != user.id or record.deleted or record.version != expected_version:
        return
    memories = list(
        db.scalars(
            select(Memory).where(
                Memory.user_id == user.id, Memory.record_id == rid, Memory.record_version == expected_version
            )
        )
    )
    if not memories:
        return
    vectors = Bailian(db, user.id).embeddings([m.detail[:6000] for m in memories])
    db.refresh(record)
    if record.deleted or record.version != expected_version:
        db.rollback()
        return
    for memory, vector in zip(memories, vectors):
        memory.embedding, memory.vector = vector, vector
    db.commit()


def latest_diary(db, user_id, day):
    return db.scalar(
        select(Diary)
        .where(Diary.user_id == user_id, Diary.day == day)
        .order_by(Diary.version.desc())
        .limit(1)
    )


def diary_wire(diary):
    return {
        "id": diary.id,
        "day": diary.day,
        "version": diary.version,
        "paragraphs": diary.paragraphs,
        "timeline": diary.timeline,
        "pending": diary.pending,
    }


@serialized(lambda db, user, day: f"diary:{user.id}:{day}")
def generate_diary(db, user, day):
    zone = ZoneInfo(preferences(user).timezone)
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), zone)
    records = list(
        db.scalars(
            select(Record)
            .where(
                Record.user_id == user.id,
                Record.deleted.is_(False),
                Record.superseded_by == "",
                Record.occurred_at >= start.astimezone(timezone.utc),
                Record.occurred_at < (start + timedelta(days=1)).astimezone(timezone.utc),
            )
            .order_by(Record.occurred_at)
        )
    )
    records = [r for r in records if r.text]
    previous = latest_diary(db, user.id, day)
    if not records:
        return None
    versions = {r.id: r.version for r in records}
    timeline = []
    for record in records:
        memories = list(
            db.scalars(
                select(Memory).where(
                    Memory.user_id == user.id,
                    Memory.record_id == record.id,
                    Memory.record_version == record.version,
                )
            )
        )
        if memories:
            for memory in memories:
                quote = record.text[memory.source_start : memory.source_end]
                timeline.append(
                    {
                        "id": memory.id,
                        "time": aware(record.occurred_at).astimezone(zone).strftime("%H:%M"),
                        "title": memory.title,
                        "text": memory.detail,
                        "kind": memory.kind,
                        "people": memory.people,
                        "topics": memory.topics,
                        "sources": [source(db, record, quote)],
                    }
                )
        else:
            timeline.append(
                {
                    "id": record.id,
                    "time": aware(record.occurred_at).astimezone(zone).strftime("%H:%M"),
                    "title": record.text[:30],
                    "text": record.text,
                    "kind": "knowledge" if record.source_type == "external" else "event",
                    "people": [],
                    "topics": [],
                    "sources": [source(db, record)],
                }
            )
    paragraphs = [
        {
            "id": r.id,
            "text": r.text,
            "edited": False,
            "kind": "external" if r.source_type == "external" else "personal",
            "sources": [source(db, r)],
        }
        for r in records
    ]
    if settings.text_ai_enabled:
        result = Bailian(db, user.id).json(
            "整理第一人称真实日记，保留个人语气，不添加未发生的事情。外部知识独立成段。返回 paragraphs 数组，"
            "每项含 text, kind(personal/external), record_ids。不要添加建议。",
            [
                {
                    "record_id": r.id,
                    "source_type": r.source_type,
                    "text": r.text,
                    "time": aware(r.occurred_at).astimezone(zone).isoformat(),
                }
                for r in records
            ],
        )
        by_id = {r.id: r for r in records}
        generated = []
        for paragraph in result.get("paragraphs", []):
            ids = paragraph.get("record_ids", [])
            if not ids or any(rid not in by_id for rid in ids):
                continue
            types = {by_id[rid].source_type for rid in ids}
            if len(types) != 1:
                continue
            generated.append(
                {
                    "id": uid(),
                    "text": str(paragraph.get("text", ""))[:20000],
                    "edited": False,
                    "kind": "external" if "external" in types else "personal",
                    "sources": [source(db, by_id[rid]) for rid in ids],
                }
            )
        if generated:
            paragraphs = generated
    for record in records:
        db.refresh(record)
        if record.deleted or record.version != versions[record.id]:
            return None
    pending = []
    if previous:
        edited = [p for p in previous.paragraphs if p.get("edited")]
        if edited:
            covered = {(s["record_id"], s["version"], s["quote"]) for p in edited for s in p["sources"]}
            pending = [
                p
                for p in paragraphs
                if any((s["record_id"], s["version"], s["quote"]) not in covered for s in p["sources"])
            ]
            paragraphs = edited + [
                p
                for p in previous.paragraphs
                if not p.get("edited")
                and all(versions.get(s["record_id"]) == s["version"] for s in p["sources"])
            ]
    diary = Diary(
        user_id=user.id,
        day=day,
        version=(previous.version + 1 if previous else 1),
        paragraphs=paragraphs,
        timeline=timeline,
        pending=pending,
    )
    db.add(diary)
    db.flush()
    changed(db, user.id, "diary", diary.id)
    db.commit()
    return diary
