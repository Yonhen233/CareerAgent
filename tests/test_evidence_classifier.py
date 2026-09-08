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


def test_retrieval_prior_is_soft_and_explainable():
    classifier = EvidenceClassifier()

    prior, classification = classifier.retrieval_prior(
        "Experience: supported FastAPI delivery, tests and monitoring.",
        chunk_type="experience",
    )
    assert classification.evidence_type == "shipped_project"
    assert prior == 0.05

    weak_prior, weak_classification = classifier.retrieval_prior(
        "Planned learning: wants to study FastAPI next semester, no implementation yet.",
        chunk_type="education",
    )
    assert weak_classification.evidence_type == "missing_skill_disclosure"
    assert weak_prior == -0.15

    mixed_prior, mixed_classification = classifier.retrieval_prior(
        "Built an Agent demo with Python, but did not implement evaluation metrics.",
        chunk_type="project",
    )
    assert mixed_classification.evidence_type == "mixed_delivery_disclosure"
    assert mixed_prior == -0.05
