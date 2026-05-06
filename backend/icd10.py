"""ICD-10 autosuggest endpoint. No auth — frontend hits this on every keystroke."""
from typing import Optional
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from icd10_data import ICD10_CODES
from rate_limit import limiter

router = APIRouter(prefix="/api/icd10", tags=["icd10"])


class Icd10Hit(BaseModel):
    code: str
    name_ru: str
    name_en: str


# Pre-compute lowercase searchable views once. ICD10_CODES is static at import.
_INDEX: list[tuple[str, str, str, str, str]] = [
    (code, name_ru, name_en, code.lower(), (name_ru + " " + name_en).lower())
    for code, name_ru, name_en in ICD10_CODES
]


def _search(query: str, limit: int) -> list[Icd10Hit]:
    """Match query against code prefix first (priority), then name substring.
    Code matches outrank name matches so typing 'I10' surfaces I10 immediately
    even though many names contain '10'."""
    q = (query or "").strip().lower()
    if not q:
        return []

    code_hits: list[Icd10Hit] = []
    name_hits: list[Icd10Hit] = []
    code_seen: set[str] = set()

    for code, name_ru, name_en, code_lc, name_lc in _INDEX:
        if code_lc.startswith(q) or code_lc.replace(".", "").startswith(q):
            code_hits.append(Icd10Hit(code=code, name_ru=name_ru, name_en=name_en))
            code_seen.add(code)
            if len(code_hits) >= limit:
                break
        elif q in name_lc:
            name_hits.append(Icd10Hit(code=code, name_ru=name_ru, name_en=name_en))

    # Fill the remaining slots with name matches, skipping code-match dupes.
    out = list(code_hits)
    for hit in name_hits:
        if len(out) >= limit:
            break
        if hit.code in code_seen:
            continue
        out.append(hit)
    return out


@router.get("/search", response_model=list[Icd10Hit])
@limiter.limit("300/minute")
def search_icd10(
    request: Request,
    q: str = Query("", description="Code prefix or name substring (RU or EN)"),
    limit: int = Query(10, ge=1, le=50),
    lang: Optional[str] = Query(None, description="ru | en — kept for API forward-compat; backend already returns both names"),
):
    return _search(q, limit)


@router.get("/{code}", response_model=Icd10Hit)
@limiter.limit("300/minute")
def get_icd10(request: Request, code: str):
    """Lookup a single code. Returns 404 if unknown."""
    code_uc = code.upper()
    for c, name_ru, name_en in ICD10_CODES:
        if c == code_uc:
            return Icd10Hit(code=c, name_ru=name_ru, name_en=name_en)
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Код ICD-10 не найден")
