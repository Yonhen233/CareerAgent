from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.core.database import SessionLocal
from app.models.entities import Job, Profile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals" / "real_profile_job_relevance_annotations.json"
DEFAULT_OUTPUT = ROOT / "evals" / "results" / "real_profile_job_relevance_eval.json"


def evaluate(dataset_path: Path) -> dict:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    source_profile_id = int(dataset["source_profile"]["profile_id"])
    annotations = dataset.get("annotations") or []
    db = SessionLocal()
    try:
        profile = db.query(Profile).filter(Profile.id == source_profile_id).first()
        if profile is None:
            raise ValueError(f"Profile {source_profile_id} does not exist")
        rows = []
        for annotation in annotations:
            job_id = int(annotation["job_id"])
            job = db.query(Job).filter(Job.id == job_id).first()
            if job is None:
                raise ValueError(f"Job {job_id} does not exist")
            rows.append(
                {
                    "annotation_id": annotation["id"],
                    "profile_id": profile.id,
                    "job_id": job.id,
                    "job_title": job.title,
                    "company": job.company,
                    "label": int(annotation["label"]),
                    "decision": annotation["decision"],
                    "has_raw_jd": bool((job.raw_jd_text or "").strip()),
                    "source": job.source,
                }
            )
    finally:
        db.close()

    label_counts = Counter(row["label"] for row in rows)
    result = {
        "evaluation_type": "real_profile_job_manual_relevance",
        "dataset": str(dataset_path.relative_to(ROOT)).replace("\\", "/"),
        "profile_id": source_profile_id,
        "annotation_count": len(rows),
        "label_distribution": {str(key): value for key, value in sorted(label_counts.items())},
        "strong_fit_count": sum(row["label"] == 3 for row in rows),
        "non_empty_jd_rate": round(sum(row["has_raw_jd"] for row in rows) / max(len(rows), 1), 4),
        "rows": rows,
        "note": "这是人工相关性基准集，不把人工标签冒充模型分数；后续检索/排序实验应在同一岗位快照上计算 Recall@K、MRR 或 nDCG。",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
