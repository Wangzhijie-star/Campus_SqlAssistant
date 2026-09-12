"""Ten paired campus queries; run inside SQLBot's Python 3.11 environment.

--check validates the fixture/metrics without importing SQLBot or accessing a DB.
Live mode uses the real terminology retriever, with read-only DB transactions.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

CASES = [
    ("H1", "历史", "哪些同学以前有课没考过，后来通过的也算？",
     "查询历史上出现过课程不及格的学生，包括后来已经通过的学生。", ["曾不及格"]),
    ("H2", "历史", "想看看2404班以前没考过的那些课，补过了的也别漏掉。",
     "查询2404班历史上未通过的课程记录，包括后来补考通过的记录。", ["曾不及格"]),
    ("H3", "历史", "有些课后来已经考过了，我还是想查学生以前没通过的所有课程记录。",
     "查询学生历史上未通过的所有课程记录，不因后来通过而排除。", ["曾不及格"]),
    ("C1", "当前", "哪些课程到现在还没有通过？已经补考通过的不要。",
     "查询当前仍未通过的课程。", ["尚不及格"]),
    ("C2", "当前", "帮我看看2404班现在还有谁有课没过，后来已经考过的就不用算了。",
     "查询2404班当前仍有课程未通过的学生。", ["尚不及格"]),
    ("C3", "当前", "我只想看还欠着没通过的课程，之前没过但现在过了的排除。",
     "查询当前仍未通过的课程，排除后来已经通过的课程。", ["尚不及格"]),
    ("M1", "双意图", "分别列出以前没考过的课和现在还没考过的课，前一份要包括后来考过的。",
     "分别查询历史未通过课程记录（包含后来通过的）和当前仍未通过课程记录。", ["曾不及格", "尚不及格"]),
    ("M2", "双意图", "2404班以前有过课没通过的学生有多少？现在还有课没过的又有多少？第一项补过的也算。",
     "分别统计2404班历史上有课程未通过的学生数（包含后来通过的）和当前仍有课程未通过的学生数。", ["曾不及格", "尚不及格"]),
    ("N1", "无关", "食堂今天有什么菜？", "查询今天食堂供应的菜品。", []),
    ("N2", "无关", "图书馆今天几点关门？", "查询图书馆今天的闭馆时间。", []),
]


def metrics(gold, actual):
    gold, actual = set(gold), set(actual)
    hit = len(gold & actual)
    return {"recall": hit / len(gold) if gold else None,
            "precision": hit / len(actual) if actual else (0.0 if gold else None),
            "all_hit": gold <= actual if gold else None,
            "exact": gold == actual,
            "false_positive": bool(actual) if not gold else None}


def aggregate(rows):
    result = {"count": len(rows)}
    for key in ("recall", "precision", "all_hit", "exact", "false_positive", "vector_top1_hit"):
        values = [r[key] for r in rows if r[key] is not None]
        result[key] = sum(values) / len(values) if values else None
    return result


def check():
    assert len(CASES) == 10 and len({c[0] for c in CASES}) == 10
    for _, _, original, rewrite, gold in CASES:
        assert original.strip() and rewrite.strip()
        assert set(gold) <= {"曾不及格", "尚不及格"}
    assert metrics(["a", "b"], ["a", "c"])["recall"] == 0.5
    assert metrics(["a"], ["a", "b", "b"])["precision"] == 0.5
    assert metrics([], ["a"])["recall"] is None
    assert metrics([], ["a"])["false_positive"] is True
    assert metrics(["a"], [])["precision"] == 0
    print("检查通过：10组配对题，指标边界检查通过。尚未运行真实召回。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--oid", type=int, default=1)
    args = parser.parse_args()
    if args.check:
        check()
        return

    root = Path(__file__).resolve().parents[2]
    os.chdir(root / "backend")
    sys.path.insert(0, str(root / "backend"))
    from sqlalchemy import event, text
    from sqlmodel import Session
    from common.core.config import settings
    from common.core.db import engine
    from apps.ai_model.embedding import EmbeddingModelCache, local_embedding_model
    from apps.terminology.curd import terminology as retriever

    if not settings.EMBEDDING_ENABLED:
        raise RuntimeError("向量检索未开启，本轮必须开启。")

    def readonly(connection):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")

    event.listen(engine, "begin", readonly)
    config = {"oid": args.oid, "threshold": settings.EMBEDDING_TERMINOLOGY_SIMILARITY,
              "top_k": settings.EMBEDDING_TERMINOLOGY_TOP_COUNT,
              "model_path": local_embedding_model.name,
              "source_sha256": hashlib.sha256(Path(retriever.__file__).read_bytes()).hexdigest(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    rows = []
    scope = "oid=:oid AND enabled=true AND (specific_ds=false OR specific_ds IS NULL)"
    try:
        with Session(engine) as session:
            corpus = [dict(r) for r in session.execute(text(
                "SELECT id,pid,word,description,embedding IS NOT NULL AS ready, "
                "vector_dims(embedding) AS dimensions FROM terminology WHERE " + scope + " ORDER BY id"
            ), {"oid": args.oid}).mappings()]
            roots = {r["id"]: r["word"] for r in corpus if r["pid"] is None}
            if len(roots) != 2 or set(roots.values()) != {"曾不及格", "尚不及格"}:
                raise RuntimeError("本轮要求当前范围只有曾不及格、尚不及格两个主术语。")
            if not all(r["ready"] and r["dimensions"] == 768 for r in corpus):
                raise RuntimeError("存在缺失向量或维度不符的术语，请先检查。")
            by_word = {r["word"]: roots[r["pid"] if r["pid"] is not None else r["id"]] for r in corpus}
            model = EmbeddingModelCache.get_model()
            print("阈值:", config["threshold"], "Top K:", config["top_k"], "向量记录数:", len(corpus), flush=True)
            for case_id, category, original, rewritten, gold in CASES:
                for variant, question in [("原问题", original), ("人工改写", rewritten)]:
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        result = retriever.select_terminology_by_word(session, question, oid=args.oid)
                    if "Traceback (most recent call last)" in stderr.getvalue():
                        raise RuntimeError("源码内部异常: " + case_id + "/" + variant + "\n" + stderr.getvalue())
                    actual = sorted({by_word[w] for group in result for w in group["words"]})
                    vector = model.embed_query(question)
                    ranked = [dict(r) for r in session.execute(text(
                        "SELECT id,pid,word,1-(embedding <=> :v) AS similarity FROM terminology WHERE "
                        + scope + " AND embedding IS NOT NULL ORDER BY similarity DESC,id ASC"
                    ), {"oid": args.oid, "v": str(vector)}).mappings()]
                    for r in ranked:
                        r["parent_word"] = by_word[r["word"]]
                        r["similarity"] = float(r["similarity"])
                    # Diagnostics: exact source ILIKE semantics, no settings toggling.
                    literal = session.execute(text(
                        "SELECT word FROM terminology WHERE " + scope + " AND :q ILIKE '%' || word || '%'"
                    ), {"oid": args.oid, "q": question}).scalars().all()
                    item = {"id": case_id, "category": category, "variant": variant,
                            "question": question, "expected": gold, "actual": actual,
                            "lexical_actual": sorted({by_word[w] for w in literal}),
                            "vector_top1_hit": (ranked[0]["parent_word"] in gold) if gold and ranked else (False if gold else None),
                            "ranking": ranked, **metrics(gold, actual)}
                    rows.append(item)
                    print(case_id, variant, "预期:", ",".join(gold) or "空", "实际:", ",".join(actual) or "空", flush=True)
    finally:
        event.remove(engine, "begin", readonly)
        engine.dispose()

    report = {"created_at": datetime.now(timezone.utc).isoformat(), "config": config,
              "note": "人工改写开发集，非自动改写质量或泛化评估；保持术语库不变。", "corpus": corpus,
              "fixture": CASES, "cases": rows, "summary": {}, "by_category": {}}
    for variant in ("原问题", "人工改写"):
        selected = [r for r in rows if r["variant"] == variant]
        report["summary"][variant] = aggregate(selected)
        report["by_category"][variant] = {cat: aggregate([r for r in selected if r["category"] == cat])
                                             for cat in sorted({r["category"] for r in selected})}
    out = root / "tools/campus_recall/reports" / datetime.now().strftime("rewrite-%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 10题人工改写对照", "", report["note"], "",
             "Recall/全命中仅对8道正例取宏平均；精确率对有返回或有答案的题取平均，正例无返回记0，负例无返回不计。",
             "完全匹配对全部10题统计；误召回率仅对2道负例统计。向量Top1命中仅统计8道正例，不代表最终集合精确率。",
             "", "## 汇总", "", "```json", json.dumps(report["summary"], ensure_ascii=False, indent=2), "```", "",
             "## 逐题结果", "", "|题号|版本|问题|标准答案|实际结果|词语匹配结果|", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("|" + "|".join([r["id"], r["variant"], r["question"],
                     ",".join(r["expected"]) or "空", ",".join(r["actual"]) or "空",
                     ",".join(r["lexical_actual"]) or "空"]) + "|")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n汇总（小数，例如0.5表示50%）：")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("\n报告目录：", out)


if __name__ == "__main__":
    main()
