"""Drug autosuggest endpoint. No auth — frontend hits this on every keystroke."""
from fastapi import APIRouter, Query
from pydantic import BaseModel

from drugs_data import DRUGS

router = APIRouter(prefix="/api/drugs", tags=["drugs"])


class DrugHit(BaseModel):
    name_ru: str
    name_en: str
    group_ru: str
    group_en: str
    common_doses: list[str]


# Pre-computed lowercase index. DRUGS is static at import.
_INDEX: list[tuple[str, str, str, str, list[str], str]] = [
    (name_ru, name_en, group_ru, group_en, doses, (name_ru + " " + name_en).lower())
    for name_ru, name_en, group_ru, group_en, doses in DRUGS
]


def _search(query: str, limit: int) -> list[DrugHit]:
    """Prefix matches (typed-from-start) win over substring matches so 'амок'
    surfaces 'Амоксициллин' / 'Амоксиклав' before 'Цефазолин'."""
    q = (query or "").strip().lower()
    if not q:
        return []

    prefix_hits: list[DrugHit] = []
    sub_hits: list[DrugHit] = []
    for name_ru, name_en, group_ru, group_en, doses, idx in _INDEX:
        ru_lc = name_ru.lower()
        en_lc = name_en.lower()
        if ru_lc.startswith(q) or en_lc.startswith(q):
            prefix_hits.append(DrugHit(name_ru=name_ru, name_en=name_en, group_ru=group_ru, group_en=group_en, common_doses=doses))
            if len(prefix_hits) >= limit:
                break
        elif q in idx:
            sub_hits.append(DrugHit(name_ru=name_ru, name_en=name_en, group_ru=group_ru, group_en=group_en, common_doses=doses))

    out = list(prefix_hits)
    for h in sub_hits:
        if len(out) >= limit:
            break
        out.append(h)
    return out


@router.get("/search", response_model=list[DrugHit])
def search_drugs(
    q: str = Query("", description="Drug name prefix or substring (RU or EN)"),
    limit: int = Query(10, ge=1, le=50),
):
    return _search(q, limit)
