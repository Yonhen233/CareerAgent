from app.models.entities import TaskRun
from app.services.task_queue import TaskQueueService


def test_task_queue_records_llm_workflow_progress(db_session):
    service = TaskQueueService()
    task = service.create_llm_workflow_task(
        db_session,
        case_limit=3,
        resume_from_last_completed=False,
        trace_path="data/runtime/test_trace.jsonl",
    )

    assert task.status == "queued"
    assert task.progress_json["case_count"] == 3

    service.update_progress(
        task.id,
        {
            "case_count": 3,
            "completed_cases": 2,
            "remaining_cases": 1,
            "status": "running",
            "current_case": "case_b",
            "end_to_end_pass_rate": 1.0,
        },
        evaluation_run_id=42,
        db=db_session,
    )

    row = db_session.query(TaskRun).filter(TaskRun.id == task.id).first()
    assert row is not None
    assert row.progress_json["percent"] == 0.6667
    assert row.progress_json["evaluation_run_id"] == 42
