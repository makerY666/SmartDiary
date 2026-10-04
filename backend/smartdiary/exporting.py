import hashlib
import json
from datetime import date, datetime, timezone
from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from sqlalchemy import select

from . import storage
from .domain import aware, changed, diary_wire, enqueue, erase_record, record_wire, write_record
from .models import Attachment, Diary, Memory, Message, Person, Record, RecordRevision, Reminder, Tombstone
from .schemas import Preferences, RecordWrite


def export_bundle(db, user):
    def serial(row):
        from datetime import datetime

        return {
            column.name: aware(getattr(row, column.name)).isoformat()
            if isinstance(getattr(row, column.name), datetime)
            else getattr(row, column.name)
            for column in row.__table__.columns
            if column.name not in {"user_id", "embedding", "vector", "storage_key"}
        }

    records = list(db.scalars(select(Record).where(Record.user_id == user.id, Record.deleted.is_(False))))
    attachments = list(db.scalars(select(Attachment).where(Attachment.user_id == user.id)))
    latest = {}
    for diary in db.scalars(select(Diary).where(Diary.user_id == user.id).order_by(Diary.version)):
        latest[diary.day] = diary
    data = {
        "format": "smartdiary-export-v1",
        "user_id": user.id,
        "settings": user.settings,
        "records": [record_wire(r, db) for r in records],
        "diaries": [diary_wire(d) for d in latest.values()],
        "diary_history": [
            diary_wire(d)
            for d in db.scalars(select(Diary).where(Diary.user_id == user.id).order_by(Diary.version))
        ],
        "revisions": [
            serial(r) for r in db.scalars(select(RecordRevision).where(RecordRevision.user_id == user.id))
        ],
        "memories": [serial(m) for m in db.scalars(select(Memory).where(Memory.user_id == user.id))],
        "people": [serial(p) for p in db.scalars(select(Person).where(Person.user_id == user.id))],
        "messages": [serial(m) for m in db.scalars(select(Message).where(Message.user_id == user.id))],
        "reminders": [serial(r) for r in db.scalars(select(Reminder).where(Reminder.user_id == user.id))],
        "tombstones": [
            t.record_id for t in db.scalars(select(Tombstone).where(Tombstone.user_id == user.id))
        ],
        "attachments": [
            {
                "id": a.id,
                "record_id": a.record_id,
                "mime_type": a.mime_type,
                "duration_seconds": a.duration_seconds,
                "position": a.position,
                "preserve_text": a.preserve_text,
                "sha256": a.sha256,
                "extracted_text": a.extracted_text,
            }
            for a in attachments
        ],
    }
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(data, ensure_ascii=False, indent=2))
        for diary in latest.values():
            text = "# " + diary.day + "\n\n" + "\n\n".join(p["text"] for p in diary.paragraphs)
            if diary.pending:
                text += "\n\n## 待合并补充\n\n" + "\n\n".join(p["text"] for p in diary.pending)
            bundle.writestr(f"diaries/{diary.day}.md", text)
        for attachment in attachments:
            bundle.writestr("attachments/" + attachment.id, storage.read(attachment.storage_key))
    return output.getvalue()


def validate_manifest(bundle, manifest):
    """Validate the entire archive before record writes (which commit individually)."""
    Preferences(**manifest.get("settings", {}))
    for item in manifest["records"]:
        RecordWrite(
            **{
                key: item[key]
                for key in ("id", "kind", "text", "url", "occurred_at", "recorded_at", "source_type")
            }
        )
        if item.get("parent_record_id"):
            UUID(item["parent_record_id"])
        if item.get("relation", "followup") not in {"followup", "result"}:
            raise ValueError("invalid relation")
        if item.get("superseded_by"):
            UUID(item["superseded_by"])
        if not isinstance(item.get("version", 1), int) or item.get("version", 1) < 1:
            raise ValueError("invalid version")
    for rid in manifest.get("tombstones", []):
        UUID(rid)
    for item in manifest.get("attachments", []):
        aid = str(UUID(item["id"]))
        UUID(item["record_id"])
        content = bundle.read("attachments/" + aid)
        if (
            not storage.valid_media(content, item["mime_type"])
            or hashlib.sha256(content).hexdigest() != item["sha256"]
        ):
            raise ValueError("invalid attachment")
        float(item["duration_seconds"])
        if not isinstance(item.get("preserve_text", False), bool):
            raise ValueError("invalid preserved text flag")
        if not isinstance(item.get("position", 0), int) or not 0 <= item.get("position", 0) <= 100000:
            raise ValueError("invalid media position")
        if (
            not isinstance(item.get("extracted_text", ""), str)
            or len(item.get("extracted_text", "")) > 100000
        ):
            raise ValueError("invalid extraction cache")
    for item in manifest.get("diary_history", manifest.get("diaries", [])):
        date.fromisoformat(item["day"])
        int(item["version"])
        for paragraph in item["paragraphs"] + item["timeline"] + item.get("pending", []):
            str(paragraph["text"])
            for ref in paragraph.get("sources", []):
                UUID(ref["record_id"])
            for rid in paragraph.get("pending_source_ids", []):
                UUID(rid)
    required = {
        "people": ("name", "aliases", "confirmed"),
        "memories": (
            "record_id",
            "title",
            "detail",
            "kind",
            "people",
            "topics",
            "confirmed",
            "source_start",
            "source_end",
        ),
        "reminders": ("text", "due_at", "confirmed", "delivered", "completed"),
        "messages": ("role", "text", "sources", "proactive", "created_at"),
        "revisions": ("record_id", "version", "kind", "snapshot"),
    }
    for kind, keys in required.items():
        for item in manifest.get(kind, []):
            UUID(item["id"])
            for key in keys:
                item[key]
            if "record_id" in item:
                UUID(item["record_id"])
            for key in ("due_at", "created_at"):
                if key in item and datetime.fromisoformat(item[key]).tzinfo is None:
                    raise ValueError("timezone required")
            if kind == "messages":
                for ref in item["sources"]:
                    UUID(ref["record_id"])


def restore_bundle(db, user, data):
    try:
        with ZipFile(BytesIO(data)) as bundle:
            infos = bundle.infolist()
            if (
                len(infos) > 5000
                or sum(i.file_size for i in infos) > 100 * 1024 * 1024
                or any(i.file_size > 20 * 1024 * 1024 for i in infos)
            ):
                raise ValueError("archive limits")
            manifest = json.loads(bundle.read("manifest.json"))
            if manifest.get("format") != "smartdiary-export-v1" or manifest.get("user_id") != user.id:
                raise HTTPException(400, "只支持恢复当前账户导出的 SmartDiary 数据")
            validate_manifest(bundle, manifest)
            pending_records = list(manifest["records"])
            ordered = []
            while pending_records:
                remaining_ids = {r["id"] for r in pending_records}
                ready = [
                    r
                    for r in pending_records
                    if not r.get("parent_record_id") or r["parent_record_id"] not in remaining_ids
                ]
                if not ready:
                    raise ValueError("cyclic record links")
                ordered.extend(ready)
                pending_records = [r for r in pending_records if r not in ready]
            restored, skipped = 0, 0
            matching_records = set()

            for rid in manifest.get("tombstones", []):
                rid = str(UUID(rid))
                existing = db.get(Record, rid)
                if existing and existing.user_id == user.id and not existing.deleted:
                    erase_record(db, user, rid, commit=False)
                else:
                    storage.mark_deleted(user.id, rid)
            for item in ordered:
                if storage.was_deleted(user.id, item["id"]):
                    skipped += 1
                    continue
                existing = db.get(Record, item["id"])
                if existing:
                    if (
                        existing.user_id == user.id
                        and not existing.deleted
                        and existing.text == item["text"]
                        and existing.version == item.get("version", 1)
                    ):
                        matching_records.add(existing.id)
                    skipped += 1
                    continue
                payload = RecordWrite(
                    **{
                        key: item[key]
                        for key in ("id", "kind", "text", "url", "occurred_at", "recorded_at", "source_type")
                    },
                    parent_record_id=item.get("parent_record_id")
                    if item.get("parent_record_id")
                    and not storage.was_deleted(user.id, item["parent_record_id"])
                    else None,
                    relation=item.get("relation", "followup"),
                )
                write_record(db, user, payload, schedule_processing=False, commit=False)
                record = db.get(Record, item["id"])
                record.original_text = item.get("original_text", item["text"])
                record.superseded_by = item.get("superseded_by", "")
                record.version = item.get("version", 1)
                record.status = item.get("status", "pending")
                record.error = item.get("error", "")
                matching_records.add(record.id)
                enqueue(
                    db,
                    user.id,
                    "index" if record.status in {"ready", "ready_basic"} else "process",
                    f"restore:{record.id}:{record.version}",
                    {"record_id": record.id, "version": record.version},
                )
                restored += 1
            for item in manifest.get("attachments", []):
                record = db.get(Record, item["record_id"])
                if (
                    not record
                    or record.deleted
                    or record.user_id != user.id
                    or record.id not in matching_records
                    or db.get(Attachment, item["id"])
                ):
                    continue
                attachment_id = str(UUID(item["id"]))
                content = bundle.read("attachments/" + attachment_id)
                if not storage.valid_media(content, item["mime_type"]):
                    raise ValueError("invalid media")
                key, digest = storage.put(user.id, attachment_id, content)
                if digest != item["sha256"]:
                    raise ValueError("checksum mismatch")
                db.add(
                    Attachment(
                        id=attachment_id,
                        user_id=user.id,
                        record_id=record.id,
                        mime_type=item["mime_type"],
                        storage_key=key,
                        sha256=digest,
                        size=len(content),
                        duration_seconds=item["duration_seconds"],
                        position=item.get("position", 0),
                        preserve_text=item.get("preserve_text", False),
                        extracted_text=item.get("extracted_text", ""),
                    )
                )
            existing_days = {d.day for d in db.scalars(select(Diary).where(Diary.user_id == user.id))}
            for item in manifest.get("diary_history", manifest.get("diaries", [])):
                if item["day"] in existing_days:
                    continue

                def valid(items):
                    output = []
                    for paragraph in items:
                        refs = []
                        for ref in paragraph.get("sources", []):
                            record = db.get(Record, ref["record_id"])
                            if (
                                not record
                                or record.deleted
                                or record.user_id != user.id
                                or ref.get("quote", "") not in record.text
                            ):
                                break
                            refs.append({**ref, "version": record.version})
                        else:
                            stale_ids = paragraph.get("pending_source_ids", [])
                            stale_valid = bool(stale_ids) and all(
                                (r := db.get(Record, rid)) and r.user_id == user.id and not r.deleted
                                for rid in stale_ids
                            )
                            if refs or (paragraph.get("edited") and paragraph.get("stale") and stale_valid):
                                output.append({**paragraph, "sources": refs})
                    return output

                db.add(
                    Diary(
                        user_id=user.id,
                        day=item["day"],
                        version=item["version"],
                        paragraphs=valid(item["paragraphs"]),
                        timeline=valid(item["timeline"]),
                        pending=valid(item.get("pending", [])),
                    )
                )
            for item in manifest.get("people", []):
                pid = str(UUID(item["id"]))
                if not db.get(Person, pid):
                    db.add(
                        Person(
                            id=pid,
                            user_id=user.id,
                            name=item["name"],
                            aliases=item["aliases"],
                            confirmed=item["confirmed"],
                        )
                    )
            for item in manifest.get("memories", []):
                mid = str(UUID(item["id"]))
                record = db.get(Record, item["record_id"])
                if (
                    not record
                    or record.user_id != user.id
                    or record.deleted
                    or record.superseded_by
                    or record.id not in matching_records
                    or db.get(Memory, mid)
                    or not (0 <= item["source_start"] < item["source_end"] <= len(record.text))
                ):
                    continue
                db.add(
                    Memory(
                        id=mid,
                        user_id=user.id,
                        record_id=record.id,
                        record_version=record.version,
                        title=item["title"],
                        detail=item["detail"],
                        kind=item["kind"],
                        people=item["people"],
                        topics=item["topics"],
                        confirmed=item["confirmed"],
                        source_start=item["source_start"],
                        source_end=item["source_end"],
                    )
                )
            for item in manifest.get("reminders", []):
                rid = str(UUID(item["id"]))
                if not db.get(Reminder, rid):
                    db.add(
                        Reminder(
                            id=rid,
                            user_id=user.id,
                            text=item["text"],
                            due_at=datetime.fromisoformat(item["due_at"]).astimezone(timezone.utc),
                            confirmed=item["confirmed"],
                            delivered=item["delivered"],
                            completed=item["completed"],
                        )
                    )
            for item in manifest.get("messages", []):
                mid = str(UUID(item["id"]))
                if db.get(Message, mid):
                    continue
                refs = []
                for ref in item["sources"]:
                    record = db.get(Record, ref["record_id"])
                    if (
                        not record
                        or record.deleted
                        or record.user_id != user.id
                        or record.superseded_by
                        or ref.get("quote", "") not in record.text
                    ):
                        break
                    refs.append({**ref, "version": record.version})
                else:
                    db.add(
                        Message(
                            id=mid,
                            user_id=user.id,
                            role=item["role"],
                            text=item["text"],
                            sources=refs,
                            proactive=item["proactive"],
                            created_at=datetime.fromisoformat(item["created_at"]).astimezone(timezone.utc),
                        )
                    )
            for item in manifest.get("revisions", []):
                record = db.get(Record, item["record_id"])
                if (
                    record
                    and record.user_id == user.id
                    and not record.deleted
                    and not db.get(RecordRevision, item["id"])
                ):
                    db.add(
                        RecordRevision(
                            id=item["id"],
                            user_id=user.id,
                            record_id=record.id,
                            version=item["version"],
                            kind=item["kind"],
                            snapshot=item["snapshot"],
                        )
                    )
            user.settings = Preferences(**manifest.get("settings", {})).model_dump()
            db.flush()
            for diary in db.scalars(select(Diary).where(Diary.user_id == user.id)):
                changed(db, user.id, "diary", diary.id)
            for message in db.scalars(select(Message).where(Message.user_id == user.id)):
                changed(db, user.id, "message", message.id)
            changed(db, user.id, "settings", user.id)
            db.commit()
            return {"restored": restored, "skipped": skipped}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(400, "导出包无效或附件校验失败") from None
