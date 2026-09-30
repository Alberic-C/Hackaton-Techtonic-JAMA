from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..deps import get_llm, require_webhook_key
from ..gemini_client import LLMClient
from ..schemas import IngestResponse, N8nDocumentPayload
from ..services.ingestion import ingest_document

router = APIRouter(prefix="/webhooks/n8n", tags=["ingestion"], dependencies=[Depends(require_webhook_key)])


@router.post("/documents", response_model=IngestResponse)
def receive_document(
    payload: N8nDocumentPayload,
    response: Response,
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
    settings: Settings = Depends(get_settings),
) -> IngestResponse:
    m = payload.metadata
    result = ingest_document(
        db,
        llm,
        settings,
        external_id=payload.external_id,
        title=payload.title,
        text=payload.text,
        source_type=payload.resolved_source_type(),
        reliability_score=payload.reliability_score,
        author=m.author,
        context=m.context,
        doc_date=m.date,
        source_url=str(m.source_url) if m.source_url else None,
        tags=m.tags,
    )
    response.status_code = 201 if result.status == "created" else 200
    return IngestResponse(document_id=result.document_id, chunks=result.chunks, status=result.status)
