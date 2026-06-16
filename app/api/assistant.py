from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.agents.natural_language import NaturalLanguageAgentService
from app.core.database import get_db
from app.core.llm import format_exception
from app.models.schemas import NaturalLanguageAgentRequest, NaturalLanguageAgentResponse

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post(
    "/natural-language",
    response_model=NaturalLanguageAgentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def run_natural_language_request(
    payload: NaturalLanguageAgentRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> NaturalLanguageAgentResponse:
    service = NaturalLanguageAgentService()
    try:
        run = await service.run(db, payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Natural language agent failed: {format_exception(exc)}",
        ) from exc
    output = run.output_json or {}
    if run.status == "failed":
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return NaturalLanguageAgentResponse.model_validate(output)
