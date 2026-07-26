import asyncio
import hashlib
import re
from dataclasses import dataclass

import pymupdf

from app.domain.documents import ChunkDraft, ExtractedPage, ExtractedPdf


class DocumentProcessingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PdfExtractor:
    def __init__(
        self,
        *,
        maximum_pages: int,
        maximum_characters: int,
        timeout_seconds: float = 45,
    ) -> None:
        self._maximum_pages = maximum_pages
        self._maximum_characters = maximum_characters
        self._timeout_seconds = timeout_seconds

    async def extract(self, data: bytes) -> ExtractedPdf:
        if not data.startswith(b"%PDF-"):
            raise DocumentProcessingError(
                "DOCUMENT_INVALID_PDF", "The uploaded file is not a valid PDF."
            )
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._extract_sync, data),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise DocumentProcessingError(
                "DOCUMENT_PROCESSING_TIMEOUT", "PDF processing timed out."
            ) from exc
        except DocumentProcessingError:
            raise
        except Exception as exc:
            raise DocumentProcessingError(
                "DOCUMENT_INVALID_PDF", "The uploaded PDF could not be read."
            ) from exc

    def _extract_sync(self, data: bytes) -> ExtractedPdf:
        try:
            document = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
        except Exception as exc:
            raise DocumentProcessingError(
                "DOCUMENT_INVALID_PDF", "The uploaded PDF could not be read."
            ) from exc
        with document:
            if document.needs_pass:
                raise DocumentProcessingError(
                    "DOCUMENT_ENCRYPTED", "Password-protected PDFs are not supported."
                )
            page_count = document.page_count
            if page_count <= 0:
                raise DocumentProcessingError(
                    "DOCUMENT_INVALID_PDF", "The PDF does not contain any pages."
                )
            if page_count > self._maximum_pages:
                raise DocumentProcessingError(
                    "DOCUMENT_PAGE_LIMIT_EXCEEDED",
                    f"The PDF exceeds the {self._maximum_pages}-page limit.",
                )
            pages: list[ExtractedPage] = []
            total = 0
            for index in range(page_count):
                page = document.load_page(index)  # type: ignore[no-untyped-call]
                text = self._normalize_text(page.get_text("text"))
                total += len(text)
                if total > self._maximum_characters:
                    raise DocumentProcessingError(
                        "DOCUMENT_TEXT_LIMIT_EXCEEDED",
                        "The extracted PDF text exceeds the configured limit.",
                    )
                pages.append(ExtractedPage(page_number=index + 1, text=text))
        if total == 0:
            raise DocumentProcessingError(
                "DOCUMENT_NO_EXTRACTABLE_TEXT",
                "No extractable text was found. Scanned PDFs require OCR.",
            )
        warnings = (
            ["DOCUMENT_PARTIAL_TEXT"] if sum(bool(page.text) for page in pages) < len(pages) else []
        )
        return ExtractedPdf(
            pages=pages,
            page_count=page_count,
            character_count=total,
            warnings=warnings,
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip()


@dataclass(frozen=True)
class _Piece:
    page: int
    text: str
    heading: str | None


class DeterministicPdfChunker:
    """Page-aware deterministic chunking with bounded overlap."""

    def __init__(
        self,
        *,
        target_characters: int,
        maximum_characters: int,
        overlap_characters: int,
        minimum_characters: int,
    ) -> None:
        self._target = target_characters
        self._maximum = maximum_characters
        self._overlap = overlap_characters
        self._minimum = minimum_characters

    def chunk(self, extracted: ExtractedPdf) -> list[ChunkDraft]:
        pieces = self._pieces(extracted)
        chunks: list[ChunkDraft] = []
        buffer: list[_Piece] = []
        size = 0
        for piece in pieces:
            if buffer and size + len(piece.text) + 2 > self._target:
                chunks.append(self._draft(len(chunks), buffer))
                buffer = self._overlap_pieces(buffer)
                size = sum(len(item.text) + 2 for item in buffer)
            if len(piece.text) > self._maximum:
                for part in self._split_long(piece):
                    if buffer:
                        chunks.append(self._draft(len(chunks), buffer))
                        buffer = []
                        size = 0
                    chunks.append(self._draft(len(chunks), [part]))
                continue
            buffer.append(piece)
            size += len(piece.text) + 2
            if size >= self._maximum:
                chunks.append(self._draft(len(chunks), buffer))
                buffer = self._overlap_pieces(buffer)
                size = sum(len(item.text) + 2 for item in buffer)
        if buffer:
            final = self._draft(len(chunks), buffer)
            if (
                chunks
                and len(final.text) < self._minimum
                and len(chunks[-1].text) + len(final.text) + 2 <= self._maximum
            ):
                previous = chunks.pop()
                merged = _Piece(
                    page=previous.page_start,
                    text=f"{previous.text}\n\n{final.text}",
                    heading=previous.heading,
                )
                chunks.append(self._draft(len(chunks), [merged], page_end=final.page_end))
            elif len(final.text) >= self._minimum or not chunks:
                chunks.append(final)
        return chunks

    @staticmethod
    def _pieces(extracted: ExtractedPdf) -> list[_Piece]:
        pieces: list[_Piece] = []
        heading: str | None = None
        for page in extracted.pages:
            paragraphs = re.split(r"\n{2,}", page.text)
            if len(paragraphs) == 1:
                paragraphs = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", page.text)
            for paragraph in paragraphs:
                text = paragraph.strip()
                if not text:
                    continue
                first_line = text.splitlines()[0]
                if len(first_line) <= 120 and (
                    first_line.isupper()
                    or re.fullmatch(r"(?:\d+(?:\.\d+)*)?\s*[A-Z][^.!?]{2,80}", first_line)
                ):
                    heading = first_line
                pieces.append(_Piece(page.page_number, text, heading))
        return pieces

    def _split_long(self, piece: _Piece) -> list[_Piece]:
        return [
            _Piece(piece.page, piece.text[index : index + self._maximum], piece.heading)
            for index in range(0, len(piece.text), self._maximum - self._overlap)
            if piece.text[index : index + self._maximum].strip()
        ]

    def _overlap_pieces(self, pieces: list[_Piece]) -> list[_Piece]:
        kept: list[_Piece] = []
        size = 0
        for piece in reversed(pieces):
            if kept and size + len(piece.text) > self._overlap:
                break
            kept.append(piece)
            size += len(piece.text)
        return list(reversed(kept))

    @staticmethod
    def _draft(index: int, pieces: list[_Piece], *, page_end: int | None = None) -> ChunkDraft:
        text = "\n\n".join(piece.text for piece in pieces).strip()
        return ChunkDraft(
            index=index,
            text=text,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            page_start=min(piece.page for piece in pieces),
            page_end=page_end or max(piece.page for piece in pieces),
            heading=next((piece.heading for piece in pieces if piece.heading), None),
        )
