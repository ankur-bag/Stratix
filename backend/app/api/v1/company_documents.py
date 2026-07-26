from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import Authenticated, CompanyDocumentsService
from app.domain.documents import (
    CompanyDocument,
    DocumentIngestionJob,
    DocumentListResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    UploadCompleteRequest,
    UploadRequest,
    UploadRequestResponse,
)

router = APIRouter()


@router.post(
    "/company-documents/upload-requests",
    response_model=UploadRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_request(
    payload: UploadRequest,
    context: Authenticated,
    service: CompanyDocumentsService,
) -> UploadRequestResponse:
    return await service.request_upload(context, payload)


@router.put(
    "/company-documents/{document_id}/content",
    response_model=CompanyDocument,
)
async def upload_document_content(
    document_id: str,
    request: Request,
    context: Authenticated,
    service: CompanyDocumentsService,
) -> CompanyDocument:
    return await service.upload_content(context, document_id, request.stream())


@router.post(
    "/company-documents/{document_id}/complete",
    response_model=CompanyDocument,
)
async def complete_document_upload(
    document_id: str,
    payload: UploadCompleteRequest,
    context: Authenticated,
    service: CompanyDocumentsService,
) -> CompanyDocument:
    return await service.complete_upload(context, document_id, payload)


@router.get("/company-documents", response_model=DocumentListResponse)
async def list_company_documents(
    context: Authenticated, service: CompanyDocumentsService
) -> DocumentListResponse:
    return DocumentListResponse(items=await service.list_documents(context))


@router.get(
    "/company-documents/{document_id}",
    response_model=CompanyDocument,
)
async def get_company_document(
    document_id: str,
    context: Authenticated,
    service: CompanyDocumentsService,
) -> CompanyDocument:
    return await service.get_document(context, document_id)


@router.delete(
    "/company-documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_company_document(
    document_id: str,
    context: Authenticated,
    service: CompanyDocumentsService,
) -> Response:
    await service.delete(context, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/company-documents/{document_id}/reprocess",
    response_model=DocumentIngestionJob,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_company_document(
    document_id: str,
    context: Authenticated,
    service: CompanyDocumentsService,
) -> DocumentIngestionJob:
    return await service.reprocess(context, document_id)


@router.get(
    "/document-ingestion-jobs/{job_id}",
    response_model=DocumentIngestionJob,
)
async def get_document_ingestion_job(
    job_id: str,
    context: Authenticated,
    service: CompanyDocumentsService,
) -> DocumentIngestionJob:
    return await service.get_job(context, job_id)


@router.post(
    "/company/knowledge/search",
    response_model=KnowledgeSearchResponse,
)
async def search_company_knowledge(
    payload: KnowledgeSearchRequest,
    context: Authenticated,
    service: CompanyDocumentsService,
) -> KnowledgeSearchResponse:
    return await service.search(context, payload)
