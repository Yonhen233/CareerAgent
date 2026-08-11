from app.services.evidence_classifier import EvidenceClassifier


def test_evidence_classifier_distinguishes_delivery_and_negative_evidence():
    classifier = EvidenceClassifier()

    shipped = classifier.classify("Built FastAPI RAG service and deployed evaluation metrics.", chunk_type="project")
    assert shipped.evidence_type == "metric_evidence"
    assert shipped.polarity == "positive"

    missing = classifier.classify("No MLflow or feature store experience.", chunk_type="project")
    assert missing.evidence_type == "missing_skill_disclosure"
    assert missing.polarity == "negative"

    coursework = classifier.classify("Coursework: read articles about RAG and Agent systems.", chunk_type="education")
    assert coursework.evidence_type == "coursework"

    planned = classifier.classify("Currently learning RAG from tutorials.", chunk_type="project")
    assert planned.evidence_type == "planned_learning"


def test_no_progress_trigger_is_not_misclassified_as_missing_capability():
    classification = EvidenceClassifier().classify(
        "Detected repeated tool calls without new artifacts and terminated with a typed no-progress error.",
        chunk_type="project",
    )

    assert classification.evidence_type in {"shipped_project", "adjacent_experience"}
    assert classification.polarity != "negative"


def test_mixed_project_keeps_delivered_work_and_missing_skill_boundary():
    classification = EvidenceClassifier().classify(
        "Built experiment dashboards and analyzed A/B tests, but did not implement ranking models.",
        chunk_type="project",
    )

    assert classification.evidence_type == "mixed_delivery_disclosure"
    assert classification.polarity == "mixed"
