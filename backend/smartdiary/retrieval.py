import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .ai import AIUnavailable, Bailian
from .budget import BudgetExceeded
from .config import settings
from .domain import aware, preferences, source
from .models import Memory, Person, Record, RecordLink

STOP = [
    "之前",
    "上次",
    "以前",
    "那个",
    "那家",
    "什么",
    "怎么",
    "怎么样",
    "为什么",
    "时候",
    "最后",
    "后来",
    "当时",
    "问题",
    "类似",
    "我的",
    "我",
    "的",
    "了",
    "吗",
    "是",
    "有",
    "在哪",
    "哪个",
    "最近",
    "记录",
    "请问",
]


def grams(text):
    text = text.lower()
    for word in STOP:
        text = text.replace(word, " ")
    result = set()
    for part in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", text):
        if re.fullmatch(r"[a-z0-9_]+", part):
            result.add(part)
        elif len(part) >= 2:
            result.update(part[i : i + 2] for i in range(len(part) - 1))
    return result


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / denominator if denominator else 0


def search(db, user, query, use_vectors=False):
    stmt = select(Record).where(
        Record.user_id == user.id, Record.deleted.is_(False), Record.superseded_by == "", Record.text != ""
    )
    zone = ZoneInfo(preferences(user).timezone)
    if query.day_from:
        stmt = stmt.where(
            Record.occurred_at
            >= datetime.fromisoformat(query.day_from).replace(tzinfo=zone).astimezone(timezone.utc)
        )
    if query.day_to:
        stmt = stmt.where(
            Record.occurred_at
            < (datetime.fromisoformat(query.day_to).replace(tzinfo=zone) + timedelta(days=1)).astimezone(
                timezone.utc
            )
        )
    if query.source_type:
        stmt = stmt.where(Record.source_type == query.source_type)
    records = list(db.scalars(stmt))
    by_id = {r.id: r for r in records}
    memories = list(db.scalars(select(Memory).where(Memory.user_id == user.id)))
    terms = grams(query.question)
    vector = None
    if use_vectors and settings.ai_mode == "bailian":
        try:
            vector = Bailian(db, user.id).embeddings([query.question])[0]
        except (AIUnavailable, BudgetExceeded):
            pass
    aliases = {query.person} if query.person else set()
    vector_scores = {}
    if vector and db.bind.dialect.name == "postgresql":
        nearest = db.execute(
            select(Memory.id, (1 - Memory.vector.cosine_distance(vector)).label("score"))
            .join(Record, Memory.record_id == Record.id)
            .where(
                Memory.user_id == user.id,
                Record.user_id == user.id,
                Record.deleted.is_(False),
                Record.superseded_by == "",
                Memory.record_version == Record.version,
                Memory.vector.is_not(None),
            )
            .order_by(Memory.vector.cosine_distance(vector))
            .limit(100)
        )
        vector_scores = {mid: float(score) for mid, score in nearest}
    if query.person:
        for person in db.scalars(select(Person).where(Person.user_id == user.id)):
            if query.person == person.id or query.person == person.name or query.person in person.aliases:
                aliases.update([person.name] + person.aliases)
    candidates = []
    seen = set()
    for memory in memories:
        record = by_id.get(memory.record_id)
        if not record or memory.record_version != record.version:
            continue
        quote = record.text[memory.source_start : memory.source_end]
        if query.person and not any(name in memory.people or name in quote for name in aliases):
            continue
        seen.add(record.id)
        lexical = len(terms & grams(memory.title + " " + record.text)) / max(len(terms), 1)
        semantic = vector_scores.get(memory.id, cosine(vector, memory.embedding))
        if not terms and not query.person and not query.day_from and not query.day_to:
            continue
        if lexical <= 0 and semantic < 0.4 and not query.person and not query.day_from and not query.day_to:
            continue
        candidates.append(
            {
                "id": memory.id,
                "title": memory.title,
                "text": quote,
                "kind": memory.kind,
                "people": memory.people,
                "topics": memory.topics,
                "score": round(lexical * 0.65 + max(semantic, 0) * 0.35, 4),
                "source": source(db, record),
            }
        )
    for record in records:
        if record.id in seen:
            continue
        lexical = len(terms & grams(record.text)) / max(len(terms), 1)
        if query.person and not any(name in record.text for name in aliases):
            continue
        if lexical <= 0 and not query.person and not query.day_from and not query.day_to:
            continue
        candidates.append(
            {
                "id": record.id,
                "title": record.text[:30],
                "text": record.text,
                "kind": "knowledge" if record.source_type == "external" else "event",
                "people": [],
                "topics": [],
                "score": lexical * 0.65,
                "source": source(db, record),
            }
        )
    candidates.sort(key=lambda item: (item["score"], item["source"]["occurred_at"]), reverse=True)
    return candidates[:20]


def sources_current(db, sources):
    return all(
        (record := db.get(Record, item["record_id"], populate_existing=True))
        and not record.deleted
        and not record.superseded_by
        and record.version == item["version"]
        for item in sources
    )


def stale_answer():
    return {"answer": "相关记录刚被修改，请重新查询。", "sources": [], "uncertain": True, "mode": "stale"}


def answer(db, user, query):
    results, seen = [], set()
    for hit in search(db, user, query, use_vectors=True):
        rid = hit["source"]["record_id"]
        if rid not in seen:
            results.append(hit)
            seen.add(rid)
        if len(results) == 5:
            break
    if not results:
        return {
            "answer": "没有找到足够的记录依据。可以补充人物、时间或事情中的一个具体线索。",
            "sources": [],
            "uncertain": True,
            "mode": "no_evidence",
        }
    sources = [r["source"] for r in results]
    if not query.day_from and not query.day_to:
        selected = {s["record_id"] for s in sources}
        for link in db.scalars(select(RecordLink).where(RecordLink.user_id == user.id)):
            related_id = (
                link.child_id
                if link.parent_id in selected
                else link.parent_id
                if link.child_id in selected
                else None
            )
            if not related_id or related_id in selected:
                continue
            record = db.get(Record, related_id)
            if (
                record
                and record.user_id == user.id
                and not record.deleted
                and not record.superseded_by
                and record.text
                and (not query.source_type or record.source_type == query.source_type)
            ):
                sources.append(source(db, record))
                selected.add(related_id)
            if len(sources) >= 10:
                break
    if not settings.text_ai_enabled:
        return {
            "answer": "找到这些原始记录：\n"
            + "\n".join(
                f"{aware(datetime.fromisoformat(r['source']['occurred_at'])).astimezone(ZoneInfo(preferences(user).timezone)).strftime('%Y-%m-%d')}：{r['text']}"
                for r in results
            ),
            "sources": sources,
            "uncertain": True,
            "mode": "source_excerpts",
        }
    try:
        result = Bailian(db, user.id).json(
            "回答问题，返回 answer, record_ids, uncertain。每个事实必须由资料支持。"
            "存在多个候选或时间冲突时明确说明。无法支持结论时说明未知。",
            {"question": query.question, "evidence": sources},
            operation="answer",
        )
    except (AIUnavailable, BudgetExceeded) as exc:
        if not sources_current(db, sources):
            return stale_answer()
        return {
            "answer": str(exc) + "。以下来源仍可查看。",
            "sources": sources,
            "uncertain": True,
            "mode": "source_excerpts",
        }
    if not sources_current(db, sources):
        return stale_answer()
    ids = result.get("record_ids", [])
    allowed = {s["record_id"] for s in sources}
    if not ids or any(rid not in allowed for rid in ids):
        return {
            "answer": "无法核实这次生成的引用，请查看原始记录。",
            "sources": sources,
            "uncertain": True,
            "mode": "source_excerpts",
        }
    return {
        "answer": str(result.get("answer", "无法确定")),
        "sources": [s for s in sources if s["record_id"] in ids],
        "uncertain": bool(result.get("uncertain", True)),
        "mode": "ai",
    }
