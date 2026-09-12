"""Campus terminology evaluation. Live mode calls the project's real retriever.

Run --mode check with any Python >=3.10. Live requires SQLBot's runtime.
No database writes, model generation, or student records are involved.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load_fixture(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    names = [t["word"] for t in data["terms"]]
    assert len(names) == len(set(names)), "Duplicate canonical terms"
    words = [w.casefold() for t in data["terms"] for w in [t["word"], *t["other_words"]]]
    assert len(words) == len(set(words)), "Duplicate aliases"
    ids = [c["id"] for c in data["cases"]]
    assert ids and len(ids) == len(set(ids)), "Empty/duplicate cases"
    for case in data["cases"]:
        assert case["question"].strip()
        assert len(case["expected"]) == len(set(case["expected"]))
        assert set(case["expected"]) <= set(names), case["id"]
    return data


def score(expected, actual):
    gold, got = set(expected), set(actual)
    hits = len(gold & got)
    return {"recall": hits / len(gold) if gold else None,
            "precision": hits / len(got) if got else (0.0 if gold else None),
            "all_hit": gold <= got if gold else None,
            "exact_match": gold == got,
            "false_positive": bool(got) if not gold else None,
            "missing": sorted(gold - got), "extra": sorted(got - gold)}


def summarize(rows):
    output = {"count": len(rows)}
    for key in ["recall", "precision", "all_hit", "exact_match", "false_positive"]:
        values = [r[key] for r in rows if r[key] is not None]
        output[key] = sum(values) / len(values) if values else None
    return output


def lexical_preview(data):
    # Learning aid only: equivalent literal containment for this wildcard-free corpus.
    # This is NOT a database or embedding measurement.
    rows = []
    for case in data["cases"]:
        found = [t["word"] for t in data["terms"]
                 if any(w.casefold() in case["question"].casefold()
                        for w in [t["word"], *t["other_words"]])]
        rows.append({**case, "actual": found, **score(case["expected"], found)})
    return rows


def live(data, args):
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Live mode requires the project's Python 3.11 runtime.")
    os.chdir(ROOT / "backend")  # settings resolves ../.env from this directory
    sys.path.insert(0, str(ROOT / "backend"))
    from sqlalchemy import event, text
    from sqlmodel import Session
    from common.core.db import engine
    from common.core.config import settings
    from apps.terminology.curd import terminology as retriever
    from apps.ai_model.embedding import EmbeddingModelCache

    if not settings.EMBEDDING_ENABLED:
        raise RuntimeError("EMBEDDING_ENABLED=false; cannot measure embedding recall.")

    def readonly(connection):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")

    event.listen(engine, "begin", readonly)
    runs = {"source_lexical": [], "source_hybrid": []}
    original = settings.EMBEDDING_ENABLED
    try:
        with Session(engine) as session:
            # Use the same scope as select_terminology_by_word. Restrict the fixture
            # experiment to a dedicated workspace/datasource with exactly these terms.
            scope = "(specific_ds = false OR specific_ds IS NULL)"
            params = {"oid": args.oid}
            if args.datasource is not None:
                scope = "(" + scope + " OR (specific_ds = true AND datasource_ids @> jsonb_build_array(:datasource)))"
                params["datasource"] = args.datasource
            dbrows = session.execute(text(
                "SELECT id,pid,word,description,embedding IS NOT NULL AS ready "
                "FROM terminology WHERE oid=:oid AND enabled=true AND " + scope), params).mappings().all()
            expected_words = {w for t in data["terms"] for w in [t["word"], *t["other_words"]]}
            if {r["word"] for r in dbrows} != expected_words or len(dbrows) != len(expected_words):
                raise RuntimeError("Term corpus differs from fixture. Use a dedicated test scope and enter all fixture terms/aliases; remove unrelated terms from that scope.")
            by_word = {r["word"]: r for r in dbrows}
            for term in data["terms"]:
                parent = by_word[term["word"]]
                if parent["pid"] is not None or parent["description"] != term["description"]:
                    raise RuntimeError("Term parent/description differs: " + term["word"])
                for alias in term["other_words"]:
                    if by_word[alias]["pid"] != parent["id"]:
                        raise RuntimeError("Alias parent differs: " + alias)
            if not all(r["ready"] for r in dbrows):
                raise RuntimeError("Some term embeddings are missing. Wait for embedding completion.")
            model = EmbeddingModelCache.get_model()
            probe = model.embed_query("校园成绩")
            if not probe:
                raise RuntimeError("Embedding model returned an empty vector.")
            for mode in runs:
                settings.EMBEDDING_ENABLED = mode == "source_hybrid"
                for case in data["cases"]:
                    # The source catches vector errors and prints a traceback. Treat
                    # those as a failed run rather than publishing lexical-only scores.
                    stderr = io.StringIO()
                    started = time.perf_counter()
                    with contextlib.redirect_stderr(stderr):
                        result = retriever.select_terminology_by_word(
                            session, case["question"], args.oid, args.datasource)
                    if "Traceback (most recent call last)" in stderr.getvalue():
                        raise RuntimeError("Source retriever raised a suppressed error at " + case["id"] + "; inspect local runtime/database/model configuration. No report was written.")
                    actual = []
                    for group in result:
                        matches = [t["word"] for t in data["terms"] if t["word"] in group["words"]]
                        if len(matches) != 1:
                            raise RuntimeError("Unexpected retrieved term group")
                        actual.extend(matches)
                    runs[mode].append({**case, "actual": sorted(set(actual)),
                                       "elapsed_ms": round((time.perf_counter()-started)*1000, 2),
                                       **score(case["expected"], actual)})
    finally:
        settings.EMBEDDING_ENABLED = original
        event.remove(engine, "begin", readonly)
        engine.dispose()
    return runs, {"oid": args.oid, "datasource": args.datasource,
                  "model": settings.DEFAULT_EMBEDDING_MODEL,
                  "model_path": settings.LOCAL_MODEL_PATH,
                  "threshold": settings.EMBEDDING_TERMINOLOGY_SIMILARITY,
                  "vector_top_k": settings.EMBEDDING_TERMINOLOGY_TOP_COUNT,
                  "vector_dimension": len(probe),
                  "source_sha256": hashlib.sha256(Path(retriever.__file__).read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["check", "live"], default="check")
    parser.add_argument("--fixture", type=Path, default=HERE / "fixture.json")
    parser.add_argument("--oid", type=int, help="Required in live mode: test workspace ID")
    parser.add_argument("--datasource", type=int, help="Optional test datasource ID")
    args = parser.parse_args()
    args.fixture = args.fixture.resolve()
    if args.mode == "live" and args.oid is None:
        parser.error("live mode requires --oid (do not guess your workspace ID)")
    data = load_fixture(args.fixture)
    if args.mode == "check":
        runs, config = {"local_literal_preview_NOT_embedding": lexical_preview(data)}, {}
        print("CHECK ONLY: fixture and scoring validated; no DB or embedding tested.")
    else:
        runs, config = live(data, args)
    report = {"mode": args.mode, "fixture_version": data["version"],
              "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              "created_at": datetime.now(timezone.utc).isoformat(), "config": config, "runs": {}}
    for name, rows in runs.items():
        report["runs"][name] = {"summary": summarize(rows),
            "by_category": {cat: summarize([r for r in rows if r["category"] == cat])
                            for cat in sorted({r["category"] for r in rows})}, "cases": rows}
    out = HERE / "reports" / (args.mode + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    out.mkdir(parents=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 校园术语召回评测", "", "模式：" + args.mode,
             "", "check 仅为本地词语包含演示，不代表项目实测。live 为源码最终返回集合评测，不是 Recall@K。", ""]
    for name, run in report["runs"].items():
        lines += ["## " + name, "", "```json", json.dumps(run["summary"], indent=2), "```", "",
                  "| 题号 | 问题 | 预期 | 实际 | 漏召回 | 多召回 |", "|---|---|---|---|---|---|"]
        for row in run["cases"]:
            cells = [row["id"], row["question"], ", ".join(row["expected"]), ", ".join(row["actual"]),
                     ", ".join(row["missing"]), ", ".join(row["extra"])]
            lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
        lines.append("")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("Report:", out / "report.md")


if __name__ == "__main__":
    main()
