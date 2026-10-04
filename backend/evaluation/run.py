"""Synthetic quality harness. --live uses DEEPSEEK_API_KEY without printing or storing it."""

import argparse
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from smartdiary.budget import overview
from smartdiary.companion import respond
from smartdiary.config import settings
from smartdiary.db import Base, make_engine
from smartdiary.domain import generate_diary, process_record, write_record
from smartdiary.models import Record, User, uid
from smartdiary.retrieval import answer, search
from smartdiary.schemas import Query, RecordWrite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="artifacts/evaluation.json")
    args = parser.parse_args()
    if args.live and not settings.deepseek_api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required; do not paste it into source files")
    settings.ai_mode = "deepseek" if args.live else "disabled"
    fixture = json.loads((Path(__file__).parent / "fixtures.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="smartdiary-eval-") as temp:
        settings.data_dir = Path(temp)
        engine = make_engine("sqlite:///" + str(Path(temp) / "eval.db"))
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as db:
            user = User(
                username="synthetic-evaluation",
                password_hash="not-an-account",
                settings={"proactivity": "quiet"},
            )
            db.add(user)
            db.commit()
            user_id = user.id
            ids = []
            for i, case in enumerate(fixture["cases"]):
                rid = uid()
                ids.append(rid)
                write_record(
                    db,
                    user,
                    RecordWrite(
                        id=rid,
                        text=case["text"],
                        kind="text",
                        occurred_at=datetime.fromisoformat(case["day"] + f"T{9 + i % 10:02}:00:00+08:00"),
                        recorded_at=datetime.now(timezone.utc),
                        source_type=case.get("source_type", "personal"),
                    ),
                )

        def process(rid):
            with factory() as db:
                process_record(db, db.get(User, user_id), rid, 1)

        with ThreadPoolExecutor(max_workers=2 if args.live else 1) as pool:
            list(pool.map(process, ids))
        days = sorted({c["day"] for c in fixture["cases"]})

        def diary(day):
            with factory() as db:
                return generate_diary(db, db.get(User, user_id), day)

        with ThreadPoolExecutor(max_workers=2 if args.live else 1) as pool:
            generated = list(pool.map(diary, days))
        details = []

        def evaluate(item):
            i, case = item
            with factory() as db:
                user = db.get(User, user_id)
                query = Query(question=case["question"])
                found = search(db, user, query)
                result = answer(db, user, query)
                valid = all(s["quote"] in db.get(Record, s["record_id"]).text for s in result["sources"])
                return {
                    "case": i + 1,
                    "question": case["question"],
                    "retrieval_top5": ids[i] in [r["source"]["record_id"] for r in found[:5]],
                    "expected_detail_present": case["expected"] in result["answer"],
                    "citations_valid": valid,
                    "mode": result["mode"],
                    "answer": result["answer"],
                }

        with ThreadPoolExecutor(max_workers=2 if args.live else 1) as pool:
            details = list(pool.map(evaluate, enumerate(fixture["cases"])))
        negatives = []
        with factory() as db:
            user = db.get(User, user_id)
            for q in fixture["negative_questions"]:
                result = answer(db, user, Query(question=q))
                negatives.append(
                    {
                        "question": q,
                        "no_unsupported_answer": result["uncertain"],
                        "sources": len(result["sources"]),
                        "answer": result["answer"],
                    }
                )
            if args.live:
                companion = respond(db, user, "我有点忘记当时为什么选择星河民宿了，可以帮我想想吗？")
                companion_valid = all(
                    s["quote"] in db.get(Record, s["record_id"]).text for s in companion.sources
                )
            else:
                companion_valid = None
            cost = overview(db)
        report = {
            "dataset": fixture["label"],
            "provider": "deepseek-flash" if args.live else "raw-source baseline",
            "cases": len(details),
            "days": len(generated),
            "retrieval_top5_rate": sum(d["retrieval_top5"] for d in details) / len(details),
            "expected_detail_rate": sum(d["expected_detail_present"] for d in details) / len(details),
            "citations_valid_rate": sum(d["citations_valid"] for d in details) / len(details),
            "diary_source_coverage_rate": sum(
                len({s["record_id"] for p in d.paragraphs for s in p["sources"]}) for d in generated
            )
            / len(ids),
            "companion_citations_valid": companion_valid,
            "cost": cost,
            "negative_cases": negatives,
            "details": details,
            "limitations": "Expected-detail and source-coverage checks are automated proxies, not human factuality or real-user retention scores.",
        }
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in {"details", "negative_cases"}}, ensure_ascii=True
            )
        )
        engine.dispose()


if __name__ == "__main__":
    main()
