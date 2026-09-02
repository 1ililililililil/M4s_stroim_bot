import json
import re
from dataclasses import dataclass


FALLBACK_CONFIDENCE_THRESHOLD = 0.80

TOKEN_RE = re.compile(r"[a-zа-яё0-9]{2,}", re.IGNORECASE)
STOPWORDS = {
    "а", "без", "бы", "быть", "в", "во", "вы", "да", "для", "до", "если",
    "за", "и", "из", "или", "к", "как", "ли", "на", "над", "не", "но",
    "о", "об", "от", "по", "под", "при", "про", "с", "со", "так", "то",
    "у", "что", "это", "этот", "эта", "эти", "же", "уже", "они", "он",
    "она", "мы", "можно", "нужно",
}

SYNONYM_GROUPS = (
    {"пожар", "пожаре", "пожарный", "пожарных", "огонь", "возгорание", "возгорании"},
    {"вызвать", "вызов", "позвонить", "сообщить", "сообщение"},
    {"дым", "задымление", "задымлении"},
    {"электроприбор", "электрооборудование", "прибор"},
    {"прошлые", "прошлый", "предыдущие", "предыдущий", "история"},
    {"следующий", "следующая", "будущий", "будущая"},
)


@dataclass(frozen=True)
class LocalMatch:
    entry: object
    score: float
    confidence: float
    matched_keywords: tuple[str, ...]
    layer: str
    category: str

    @property
    def confident(self) -> bool:
        return self.confidence >= FALLBACK_CONFIDENCE_THRESHOLD


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").casefold().replace("ё", "е").split())


def _canonical(token: str) -> str:
    token = token.casefold().replace("ё", "е")
    for group in SYNONYM_GROUPS:
        normalized_group = {item.replace("ё", "е") for item in group}
        if token in normalized_group:
            return min(normalized_group)
    return token


def meaningful_tokens(value: str | None) -> set[str]:
    tokens = {
        _canonical(token)
        for token in TOKEN_RE.findall(normalize_text(value))
        if token not in STOPWORDS
    }
    return tokens


def infer_category(value: str | None) -> str:
    tokens = meaningful_tokens(value)
    if tokens & {"возгорание", "огонь"}:
        return "EMERGENCY"
    if tokens & {"задымление"}:
        return "FIRE_SAFETY"
    if tokens & {"электрооборудование"}:
        return "EQUIPMENT"
    if tokens & {"обучение", "тренировка", "учеба", "подготовка"}:
        return "TRAINING"
    if tokens & {"публикация", "канал", "пост", "материал"}:
        return "CHANNEL"
    return "GENERAL"


def _token_matches(query_token: str, entry_token: str) -> bool:
    if query_token == entry_token:
        return True
    if _canonical(query_token) == _canonical(entry_token):
        return True
    return (
        len(query_token) >= 5
        and len(entry_token) >= 5
        and query_token[:5] == entry_token[:5]
    )


def score_entry(query: str, entry) -> LocalMatch | None:
    query_tokens = meaningful_tokens(query)
    question = getattr(entry, "question", None) or ""
    keywords = getattr(entry, "keywords", None) or ""
    entry_tokens = meaningful_tokens(f"{question} {keywords}")
    if not entry_tokens:
        entry_tokens = meaningful_tokens(getattr(entry, "text", ""))
    if not query_tokens or not entry_tokens:
        return None

    matched = sorted(
        query_token
        for query_token in query_tokens
        if any(_token_matches(query_token, entry_token) for entry_token in entry_tokens)
    )
    if not matched:
        return None

    query_coverage = len(matched) / len(query_tokens)
    entry_coverage = len(matched) / len(entry_tokens)
    question_tokens = meaningful_tokens(question)
    question_coverage = (
        len([token for token in question_tokens if any(
            _token_matches(token, matched_token) for matched_token in matched
        )]) / len(question_tokens)
        if question_tokens
        else 0
    )
    confidence = min(
        1.0,
        0.60 * min(1.0, entry_coverage * 2.0)
        + 0.30 * query_coverage
        + 0.10 * question_coverage,
    )
    query_normalized = normalize_text(query)
    question_normalized = normalize_text(question)
    exact_phrase = bool(
        question_normalized
        and (
            question_normalized in query_normalized
            or query_normalized in question_normalized
        )
    )
    if exact_phrase:
        confidence = min(1.0, confidence + 0.10)

    category = getattr(entry, "category", None) or infer_category(question or keywords)
    layer = "FAST" if confidence >= 0.95 or exact_phrase else "SMART"
    return LocalMatch(
        entry=entry,
        score=confidence,
        confidence=confidence,
        matched_keywords=tuple(matched),
        layer=layer,
        category=category,
    )


def select_answer_variants(entry, comment_id: int) -> list[str]:
    values = []
    raw_variants = getattr(entry, "answer_variants", None)
    if raw_variants:
        try:
            parsed = json.loads(raw_variants)
        except (TypeError, ValueError):
            parsed = []
        if isinstance(parsed, list):
            values.extend(str(value).strip() for value in parsed if str(value).strip())
    primary = (getattr(entry, "text", None) or "").strip()
    if primary and primary not in values:
        values.insert(0, primary)
    if not values:
        return []
    start = comment_id % len(values)
    return values[start:] + values[:start]