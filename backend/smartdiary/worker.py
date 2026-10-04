import argparse
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, update

from .budget import BudgetExceeded
from .companion import can_prompt, deliver_reminders, respond, weekly_prompt
from .config import settings
from .db import SessionLocal, utcnow
from .domain import aware, changed, enqueue, generate_diary, preferences, process_record, reindex_record
from .models import Job, Message, Record, User
from .retrieval import search
from .schemas import Query


def claim(db):
    now = utcnow()
    stmt = (
        select(Job)
        .where(
            Job.run_after <= now,
            or_(Job.state == "pending", and_(Job.state == "running", Job.lease_until < now)),
        )
        .order_by(Job.run_after)
        .limit(1)
    )
    if db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    job = db.scalar(stmt)
    if not job:
        return None
    from .models import uid

    token = uid()
    result = db.execute(
        update(Job)
        .where(
            Job.id == job.id, or_(Job.state == "pending", and_(Job.state == "running", Job.lease_until < now))
        )
        .values(
            state="running",
            lease_token=token,
            lease_until=now + timedelta(minutes=10),
            attempts=Job.attempts + 1,
        )
    )
    db.commit()
    return (job.id, token) if result.rowcount else None


def extend_lease(session_factory, job_id, token):
    with session_factory() as db:
        result = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.lease_token == token, Job.state == "running")
            .values(lease_until=utcnow() + timedelta(minutes=10))
        )
        db.commit()
        return bool(result.rowcount)


@contextmanager
def heartbeat(session_factory, job_id, token):
    stop = threading.Event()

    def maintain():
        while not stop.wait(60):
            try:
                if not extend_lease(session_factory, job_id, token):
                    return
            except Exception:
                # A transient database error is retried at the next tick; the lease lasts ten minutes.
                continue

    thread = threading.Thread(target=maintain, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def run_once(session_factory=SessionLocal):
    with session_factory() as db:
        claimed = claim(db)
    if not claimed:
        return False
    job_id, token = claimed
    with heartbeat(session_factory, job_id, token), session_factory() as db:
        job = db.get(Job, job_id)
        user = db.get(User, job.user_id)
        try:
            if user and job.state == "running":
                if job.kind == "process":
                    process_record(db, user, job.payload["record_id"], job.payload["version"])
                elif job.kind == "index":
                    reindex_record(db, user, job.payload["record_id"], job.payload["version"])
                elif job.kind == "diary":
                    generate_diary(db, user, job.payload["day"])
                elif job.kind == "companion":
                    record = db.get(Record, job.payload["record_id"])
                    if record and not record.deleted and preferences(user).proactivity == "companion":
                        respond(db, user, record.text, proactive=True, record_id=record.id)
                elif job.kind == "related":
                    record = db.get(Record, job.payload["record_id"])
                    if record and not record.deleted and preferences(user).proactivity == "balanced":
                        respond(db, user, record.text, proactive=True, record_id=record.id)
                elif job.kind == "weekly":
                    weekly_prompt(db, user)
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.lease_token == token, Job.state == "running")
                .values(state="done", lease_until=None, lease_token=None)
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id, populate_existing=True)
            error = exc.__class__.__name__
            if job and job.lease_token == token and job.state == "running":
                job.state = "failed" if job.attempts >= 5 else "pending"
                job.run_after, job.lease_until = (
                    utcnow() + timedelta(seconds=min(3600, 30 * 2**job.attempts)),
                    None,
                )
                job.last_error, job.lease_token = error, None
                if job.kind == "process":
                    record = db.get(Record, job.payload.get("record_id"))
                    if record and not record.deleted and record.version == job.payload.get("version"):
                        if isinstance(exc, BudgetExceeded):
                            record.status, record.error = (
                                "budget_blocked",
                                "本月 AI 额度不足，原始记录仍已保存。额度恢复后可重试。",
                            )
                        else:
                            record.status, record.error = "failed", "处理未完成，原始记录仍在，可重试"
                        changed(db, user.id, "record", record.id)
                db.commit()
    return True


def schedule(session_factory=SessionLocal, now=None):
    now = aware(now or utcnow()).astimezone(timezone.utc)
    with session_factory() as db:
        for user in db.scalars(select(User)):
            prefs = preferences(user)
            local = aware(now).astimezone(ZoneInfo(prefs.timezone))
            if local.strftime("%H:%M") >= prefs.diary_time:
                enqueue(db, user.id, "diary", f"night:{user.id}:{local.date()}", {"day": str(local.date())})
            # Recover a missed nightly tick after restart, without creating entries for empty days.
            previous_day = local.date() - timedelta(days=1)
            enqueue(db, user.id, "diary", f"night:{user.id}:{previous_day}", {"day": str(previous_day)})
            if can_prompt(db, user, now) and local.strftime("%H:%M") >= "20:30":
                start = local.replace(hour=0, minute=0, second=0, microsecond=0)
                exists = db.scalar(
                    select(Record.id)
                    .where(
                        Record.user_id == user.id,
                        Record.deleted.is_(False),
                        Record.recorded_at >= start.astimezone(timezone.utc),
                        Record.recorded_at < (start + timedelta(days=1)).astimezone(timezone.utc),
                    )
                    .limit(1)
                )
                key = f"checkin:{user.id}:{local.date()}"
                if settings.text_ai_enabled and local.weekday() == 6:
                    enqueue(db, user.id, "weekly", f"weekly:{user.id}:{local.date()}", {})
                elif exists and settings.text_ai_enabled and prefs.proactivity == "balanced":
                    recent = db.scalar(select(Record).where(Record.id == exists, Record.superseded_by == ""))
                    if recent and recent.text:
                        hits = search(db, user, Query(question=recent.text))
                        if any(
                            aware(datetime.fromisoformat(h["source"]["occurred_at"])) < start for h in hits
                        ):
                            enqueue(
                                db,
                                user.id,
                                "related",
                                f"related:{user.id}:{local.date()}",
                                {"record_id": recent.id},
                            )
                if (
                    not exists
                    and not (settings.text_ai_enabled and local.weekday() == 6)
                    and not db.scalar(select(Job.id).where(Job.dedupe_key == key))
                ):
                    message = Message(
                        user_id=user.id,
                        role="assistant",
                        text="今天有想留下的小事吗？一句话也可以。",
                        sources=[],
                        proactive=True,
                        created_at=now,
                    )
                    db.add(message)
                    db.flush()
                    changed(db, user.id, "message", message.id)
                    db.add(Job(user_id=user.id, kind="checkin", dedupe_key=key, payload={}, state="done"))
            deliver_reminders(db, user, now)
        db.commit()


def main():
    settings.validate_production()
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        schedule()
        while run_once():
            pass
        return
    last_schedule = 0.0
    while True:
        if time.monotonic() - last_schedule >= 60:
            schedule()
            last_schedule = time.monotonic()
        if not run_once():
            time.sleep(2)


if __name__ == "__main__":
    main()
