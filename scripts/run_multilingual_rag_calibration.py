from __future__ import annotations

import json
from pathlib import Path

from app.core.database import SessionLocal, init_db
from app.services.multilingual_rag_evaluation import MultilingualRAGEvaluationService


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        run = MultilingualRAGEvaluationService().run(db)
        output = Path("artifacts") / "multilingual_rag_calibration_latest.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(run.summary_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(run.summary_json, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
