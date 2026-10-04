from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .ai import AIUnavailable, Bailian
from .config import settings
from .db import utcnow
from .domain import aware, changed, preferences, source
from .locking import serialized
from .models import Message, Record, Reminder
from .retrieval import search
from .schemas import Query


def in_quiet_hours(prefs, now):
    clock = aware(now).astimezone(ZoneInfo(prefs.timezone)).time().replace(tzinfo=None)
    start, end = time.fromisoformat(prefs.quiet_start), time.fromisoformat(prefs.quiet_end)
    if start == end:
        return False
    return start <= clock < end if start < end else clock >= start or clock < end


def can_prompt(db, user, now):
    prefs = preferences(user)
    if prefs.proactivity == "quiet" or prefs.proactive_limit == 0 or in_quiet_hours(prefs, now):
        return False
    local = aware(now).astimezone(ZoneInfo(prefs.timezone))
    start = datetime.combine(local.date(), time.min, local.tzinfo)
    count = len(
        list(
            db.scalars(
                select(Message.id).where(
                    Message.user_id == user.id,
                    Message.proactive.is_(True),
                    Message.created_at >= start.astimezone(timezone.utc),
                    Message.created_at < (start + timedelta(days=1)).astimezone(timezone.utc),
                )
            )
        )
    )
    return count < prefs.proactive_limit


def wire(message):
    return {
        "id": message.id,
        "role": message.role,
        "text": message.text,
        "sources": message.sources,
        "proactive": message.proactive,
        "created_at": aware(message.created_at).isoformat(),
    }


@serialized(lambda db, user, text, proactive=False, record_id=None: f"companion:{user.id}")
def respond(db, user, text, proactive=False, record_id=None):
    if proactive and not can_prompt(db, user, utcnow()):
        return None
    if not settings.text_ai_enabled:
        if proactive:
            return None
        reply = Message(
            user_id=user.id,
            role="assistant",
            text="已保存。云端 AI 尚未启用，当前可以查看日记和搜索原始记录。",
            sources=[],
        )
    else:
        hits = search(db, user, Query(question=text[:2000]), use_vectors=True)[:5]
        evidence = [h["source"] for h in hits]
        if record_id:
            record = db.get(Record, record_id)
            if not record or record.deleted or record.user_id != user.id:
                return None
            evidence = [source(db, record)] + [s for s in evidence if s["record_id"] != record_id]
        recent = list(
            db.scalars(
                select(Message)
                .where(Message.user_id == user.id, Message.proactive.is_(False))
                .order_by(Message.created_at.desc())
                .limit(6)
            )
        )
        result = Bailian(db, user.id).json(
            "温和回应用户。只能基于给定来源联系旧记忆，不能猜测感受或诊断。返回 reply, questions(最多2个), record_ids。"
            "追問可跳过，不把建议写成事实。缺少重要细节时可询问，但不强求。",
            {
                "input": text,
                "evidence": evidence,
                "recent_dialogue_not_facts": [{"role": m.role, "text": m.text} for m in reversed(recent)],
            },
            operation="companion",
            optional=proactive,
        )
        ids = result.get("record_ids", [])
        allowed = {s["record_id"] for s in evidence}
        if any(rid not in allowed for rid in ids):
            raise AIUnavailable("回复引用无法核实")
        for s in evidence:
            record = db.get(Record, s["record_id"], populate_existing=True)
            if not record or record.deleted or record.version != s["version"]:
                return None
        db.refresh(user)
        if proactive and not can_prompt(db, user, utcnow()):
            return None
        limit = 0 if proactive and preferences(user).proactivity == "balanced" else 2
        questions = [str(q)[:500] for q in result.get("questions", [])][:limit]
        reply = Message(
            user_id=user.id,
            role="assistant",
            text=str(result.get("reply", ""))[:10000]
            + ("\n\n可以跳过：\n" + "\n".join(questions) if questions else ""),
            sources=[s for s in evidence if s["record_id"] in ids],
            proactive=proactive,
            created_at=aware(utcnow()).astimezone(timezone.utc),
        )
    db.add(reply)
    db.flush()
    changed(db, user.id, "message", reply.id)
    db.commit()
    return reply


def weekly_review(db, user):
    now = aware(utcnow()).astimezone(timezone.utc)
    records = list(
        db.scalars(
            select(Record)
            .where(
                Record.user_id == user.id,
                Record.deleted.is_(False),
                Record.superseded_by == "",
                Record.occurred_at >= now - timedelta(days=7),
                Record.text != "",
            )
            .order_by(Record.occurred_at)
        )
    )
    evidence = [source(db, r) for r in records]
    if not evidence:
        return {"text": "本周还没有可回顾的记录。", "sources": []}
    if settings.text_ai_enabled:
        result = Bailian(db, user.id).json(
            "整理一周重要事件、用户明确表达的变化和未完事项，不推测心理规律。返回 text, record_ids。",
            evidence,
            operation="weekly",
            optional=True,
        )
        ids = result.get("record_ids", [])
        allowed = {s["record_id"] for s in evidence}
        if any(rid not in allowed for rid in ids):
            raise AIUnavailable("回顾引用无法核实")
        for item in evidence:
            record = db.get(Record, item["record_id"], populate_existing=True)
            if not record or record.deleted or record.superseded_by or record.version != item["version"]:
                raise AIUnavailable("回顾来源已修改，请重新生成")
        return {
            "text": str(result.get("text", "")),
            "sources": [s for s in evidence if s["record_id"] in ids],
        }
    return {"text": "本周记录\n" + "\n".join(r.text for r in records), "sources": evidence}


def deliver_reminders(db, user, now):
    for reminder in db.scalars(
        select(Reminder).where(
            Reminder.user_id == user.id,
            Reminder.confirmed.is_(True),
            Reminder.delivered.is_(False),
            Reminder.completed.is_(False),
            Reminder.due_at <= now,
        )
    ):
        message = Message(user_id=user.id, role="reminder", text=reminder.text, sources=[])
        db.add(message)
        db.flush()
        changed(db, user.id, "message", message.id)
        reminder.delivered = True
    db.commit()


@serialized(lambda db, user: f"companion:{user.id}")
def weekly_prompt(db, user):
    if not can_prompt(db, user, utcnow()):
        return None
    review = weekly_review(db, user)
    db.refresh(user)
    if not can_prompt(db, user, utcnow()):
        return None
    message = Message(
        user_id=user.id,
        role="assistant",
        text=review["text"] if review["sources"] else "今天有想留下的小事吗？一句话也可以。",
        sources=review["sources"],
        proactive=True,
        created_at=aware(utcnow()).astimezone(timezone.utc),
    )
    db.add(message)
    db.flush()
    changed(db, user.id, "message", message.id)
    db.commit()
    return message
