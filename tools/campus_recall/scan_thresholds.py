"""Offline replay of rewrite_sop reports; no database/model/config changes."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from rewrite_sop import metrics, aggregate


def replay(case, threshold, top_k):
    ranked = sorted(case["ranking"], key=lambda r: (-r["similarity"], r["id"]))
    eligible = [r for r in ranked if r["similarity"] > threshold]
    if len(eligible) > top_k and eligible[top_k-1]["similarity"] == eligible[top_k]["similarity"]:
        raise ValueError("Top K边界存在同分，原SQL未定义同分顺序，无法可靠回放。")
    # Source LIMIT applies to alias rows BEFORE canonical term deduplication.
    actual = sorted(set(case["lexical_actual"]) | {r["parent_word"] for r in eligible[:top_k]})
    return {"id": case["id"], "variant": case["variant"], "category": case["category"],
            "question": case["question"], "expected": case["expected"], "actual": actual,
            "vector_top1_hit": case["vector_top1_hit"], **metrics(case["expected"], actual)}


def self_check():
    case = {"id": "test", "variant": "test", "category": "test", "question": "test",
            "expected": ["A"], "lexical_actual": ["L"], "vector_top1_hit": True,
            "ranking": [{"id": 1, "parent_word": "A", "similarity": .8},
                        {"id": 2, "parent_word": "A", "similarity": .7},
                        {"id": 3, "parent_word": "B", "similarity": .6}]}
    assert replay(case, .4, 2)["actual"] == ["A", "L"]
    assert replay(case, .8, 2)["actual"] == ["L"]
    assert replay(case, .4, 3)["precision"] == 1 / 3
    case["ranking"][1]["similarity"] = .8
    try:
        replay(case, .4, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("Tie was not rejected")
    print("检查通过：严格阈值、别名截断后归并、词语分支保留、同分边界。")


def pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        self_check()
        return
    if args.report is None:
        parser.error("请传入原报告目录或report.json路径")
    source = args.report.resolve()
    if source.is_dir():
        source = source / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    top_k = report["config"]["top_k"]
    original_threshold = float(report["config"]["threshold"])
    if not isinstance(top_k, int) or top_k < 1 or not math.isfinite(original_threshold):
        raise ValueError("原报告检索配置无效")
    cases = report["cases"]
    if len(cases) != 20 or len({(c["id"], c["variant"]) for c in cases}) != 20:
        raise ValueError("本SOP需要10道配对题，共20条不重复结果")
    expected_ids = {r["id"] for r in report["corpus"] if r["ready"]}
    for case in cases:
        ranked = case["ranking"]
        if {r["id"] for r in ranked} != expected_ids or len(ranked) != len(expected_ids):
            raise ValueError("排名不是完整语料，无法扫描：" + case["id"])
        if not all(math.isfinite(r["similarity"]) for r in ranked):
            raise ValueError("存在非有限相似度")
        baseline = replay(case, original_threshold, top_k)
        if set(baseline["actual"]) != set(case["actual"]):
            raise ValueError("无法复现原报告结果，停止扫描：" + case["id"] + case["variant"])
    print("基线回放校验通过：20/20条与原报告一致。")
    print("固定Top K:", top_k, "；词语匹配始终保留；仅扫描向量阈值。")
    lines = ["# 阈值离线扫描", "", "基线20/20一致；仅模拟固定语料和问题的检索结果，未修改线上配置。",
             "精确率沿用原报告约定：正例无返回记0；负例无返回不计入平均。",
             "双意图全命中仅统计M类题。此为开发集调参，不能当作独立测试成绩。", "",
             "|版本|阈值|Recall|Precision|完全匹配|双意图全命中|无关误召回|",
             "|---|---|---|---|---|---|---|"]
    runs = []
    for variant in ("原问题", "人工改写"):
        for threshold in sorted({original_threshold, .4, .45, .5, .55, .6, .65}):
            evaluated = [replay(c, threshold, top_k) for c in cases if c["variant"] == variant]
            summary = aggregate(evaluated)
            multi = aggregate([r for r in evaluated if r["category"] == "双意图"])["all_hit"]
            runs.append({"variant": variant, "threshold": threshold, "summary": summary,
                         "multi_all_hit": multi, "cases": evaluated})
            line = "|" + "|".join([variant, str(threshold), pct(summary["recall"]),
                pct(summary["precision"]), pct(summary["exact"]), pct(multi),
                pct(summary["false_positive"])]) + "|"
            lines.append(line)
    output = source.parent / datetime.now().strftime("threshold-scan-%Y%m%d-%H%M%S-%f")
    output.mkdir()
    (output / "scan.json").write_text(json.dumps({"source_report": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "config": report["config"],
        "baseline_verified": True, "runs": runs}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "scan.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines[5:]))
    print("\n扫描报告：", output / "scan.md")


if __name__ == "__main__":
    main()
