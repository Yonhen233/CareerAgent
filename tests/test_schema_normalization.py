from app.models.schemas import JDStructured, ProfileStructured


def test_profile_structured_normalizes_llm_null_leaf_fields():
    profile = ProfileStructured.model_validate(
        {
            "skills": None,
            "projects": [{"name": "CareerAgent", "description": None, "tech_stack": None, "impact": None}],
            "work_experience": [{"company": "AI Lab", "role": None, "duration": None, "details": None}],
            "raw_text": None,
        }
    )

    assert profile.skills == []
    assert profile.projects[0].description == ""
    assert profile.projects[0].impact == ""
    assert profile.projects[0].tech_stack == []
    assert profile.work_experience[0].duration == ""
    assert profile.raw_text == ""


def test_jd_structured_normalizes_llm_null_lists():
    jd = JDStructured.model_validate(
        {
            "required_skills": None,
            "preferred_skills": None,
            "responsibilities": None,
            "qualifications": None,
            "keywords": None,
        }
    )

    assert jd.required_skills == []
    assert jd.preferred_skills == []
    assert jd.responsibilities == []
    assert jd.qualifications == []
    assert jd.keywords == []
