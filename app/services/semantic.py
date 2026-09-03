"""
Semantic intent classification for Telegram comments.

Цель:
- определять несколько смыслов одновременно;
- не заменять существующую маршрутизацию FAST -> SMART -> AI;
- не отвечать автоматически на всё подряд;
- отправлять опасные/профессиональные вопросы по существующей цепочке FAQ -> SMART -> AI;
- давать осмысленные реакции на личный опыт, профессию, образование и другие комбинации смыслов.

Этот модуль не зависит от AI и не создаёт OpenAIService.
Он только классифицирует комментарий и возвращает структурированный результат.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


# ============================================================
# ENUMS
# ============================================================


class Intent(str, Enum):
    PRAISE = "praise"
    GRATITUDE = "gratitude"
    SUPPORT = "support"
    AGREEMENT = "agreement"
    DISAGREEMENT = "disagreement"
    PERSONAL_EXPERIENCE = "personal_experience"
    PROFESSION = "profession"
    EDUCATION = "education"
    FIRE_SERVICE = "fire_service"
    FIRE_SAFETY = "fire_safety"
    CONSTRUCTION = "construction"
    HUMOR = "humor"
    SURPRISE = "surprise"
    CONTINUATION_INTEREST = "continuation_interest"
    QUESTION = "question"
    ADVICE_REQUEST = "advice_request"
    CONSTRUCTIVE_CRITICISM = "constructive_criticism"
    GREETING = "greeting"


class Route(str, Enum):
    """
    Рекомендация semantic-базы.

    IMPORTANT:
    SAFE_ROUTE означает, что текущий комментарий нельзя
    отправлять в простой шаблонный semantic reply.
    """

    SKIP = "skip"
    SEMANTIC_REPLY = "semantic_reply"
    EXISTING_PIPELINE = "existing_pipeline"
    SAFE_PIPELINE = "safe_pipeline"


# ============================================================
# DATA MODELS
# ============================================================


@dataclass(frozen=True)
class IntentMatch:
    intent: Intent
    score: int
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True)
class SemanticResult:
    text: str
    intents: tuple[Intent, ...]
    matches: tuple[IntentMatch, ...]
    route: Route
    confidence: float
    is_short_reaction: bool
    requires_safe_pipeline: bool

    @property
    def has_intent(self) -> bool:
        return bool(self.intents)

    def has(self, intent: Intent) -> bool:
        return intent in self.intents


# ============================================================
# NORMALIZATION
# ============================================================


def normalize_text(text: str | None) -> str:
    """
    Нормализация комментария для keyword matching.
    """
    if not text:
        return ""

    text = text.lower().strip()

    # Убираем повторяющиеся пробелы.
    text = re.sub(r"\s+", " ", text)

    return text


def tokenize(text: str) -> tuple[str, ...]:
    """
    Простая токенизация без внешних библиотек.
    """
    return tuple(re.findall(r"[а-яёa-z0-9]+", text.lower()))


# ============================================================
# SHORT REACTIONS
# ============================================================


SHORT_REACTION_PATTERN = re.compile(
    r"^[\s🔥👏👍❤️❤😍💪😂🤣😁😄😎👌💯🎉🙌🤝🙏🚒]+$"
)


def is_short_reaction(text: str) -> bool:
    """
    Примеры:
        🔥🔥🔥
        👍
        ❤️
        👏👏👏

    Такие комментарии не должны автоматически получать
    ответ только потому, что содержат положительную эмоцию.
    """

    normalized = text.strip()

    if not normalized:
        return True

    if len(normalized) <= 12 and SHORT_REACTION_PATTERN.fullmatch(normalized):
        return True

    return False


# ============================================================
# INTENT KEYWORDS
# ============================================================


INTENT_KEYWORDS: dict[Intent, tuple[str, ...]] = {

    Intent.PRAISE: (
        "круто",
        "класс",
        "супер",
        "отлично",
        "молодец",
        "красавчик",
        "уважение",
        "респект",
        "топ",
        "красота",
        "здорово",
        "классно",
        "огонь",
        "шикарно",
        "интересно",
    ),

    Intent.GRATITUDE: (
        "спасибо",
        "благодарю",
        "благодарность",
        "спс",
        "спасиб",
        "спасибо большое",
        "огромное спасибо",
    ),

    Intent.SUPPORT: (
        "держись",
        "удачи",
        "успехов",
        "поддерживаю",
        "так держать",
        "сил тебе",
        "сил вам",
        "верим",
        "вперёд",
        "вперед",
    ),

    Intent.AGREEMENT: (
        "согласен",
        "согласна",
        "полностью согласен",
        "точно",
        "именно",
        "верно",
        "правильно",
        "тоже так считаю",
        "я тоже",
    ),

    Intent.DISAGREEMENT: (
        "не согласен",
        "не согласна",
        "неправильно",
        "спорно",
        "не думаю",
        "не совсем",
        "ошибаешься",
        "ошибаетесь",
        "другого мнения",
    ),

    Intent.PERSONAL_EXPERIENCE: (
        "я работал",
        "я работаю",
        "я тоже работал",
        "у меня было",
        "у меня тоже",
        "в моей жизни",
        "лично я",
        "сам проходил",
        "сама проходила",
        "мой опыт",
        "моя история",
        "когда я",
        "я раньше",
    ),

    Intent.PROFESSION: (
        "работаю",
        "работал",
        "профессия",
        "работа",
        "сменил работу",
        "смена профессии",
        "служу",
        "служил",
        "мчс",
        "пожарный",
        "спасатель",
        "повар",
        "водитель",
        "строитель",
    ),

    Intent.EDUCATION: (
        "учился",
        "училась",
        "учёба",
        "учеба",
        "образование",
        "университет",
        "институт",
        "колледж",
        "техникум",
        "школа",
        "диплом",
        "по образованию",
    ),

    Intent.FIRE_SERVICE: (
        "мчс",
        "пожарный",
        "пожарные",
        "спасатель",
        "караул",
        "гарнизон",
        "пожарная часть",
        "боёвка",
        "боевка",
        "дежурство",
        "выезд",
        "пожарная машина",
        "автоцистерна",
        "пожарный расчёт",
        "пожарный расчет",
    ),

    Intent.FIRE_SAFETY: (
        "пожарная безопасность",
        "огнетушитель",
        "эвакуация",
        "дым",
        "задымление",
        "горение",
        "тушить",
        "тушение",
        "возгорание",
        "проводка",
        "электрика",
        "газ",
        "утечка газа",
        "короткое замыкание",
        "огонь",
        "пожар",
    ),

    Intent.CONSTRUCTION: (
        "строим",
        "строительство",
        "фундамент",
        "бетон",
        "цемент",
        "кладка",
        "кирпич",
        "крыша",
        "дом",
        "ремонт",
        "заливка",
        "арматура",
        "опалубка",
    ),

    Intent.HUMOR: (
        "😂",
        "🤣",
        "смешно",
        "ржу",
        "ор",
        "угар",
        "шутка",
        "прикол",
        "ахах",
        "хаха",
        "😄",
    ),

    Intent.SURPRISE: (
        "ничего себе",
        "вот это",
        "неожиданно",
        "удивительно",
        "офигеть",
        "вау",
        "😮",
        "😲",
        "🤯",
    ),

    Intent.CONTINUATION_INTEREST: (
        "продолжение",
        "продолжай",
        "ждём продолжения",
        "ждем продолжения",
        "что дальше",
        "покажи дальше",
        "интересно что будет",
        "когда следующая",
        "следующая часть",
    ),

    Intent.QUESTION: (
        "?",
        "как",
        "почему",
        "зачем",
        "когда",
        "где",
        "что делать",
        "можно ли",
        "правда ли",
        "подскажите",
        "расскажите",
    ),

    Intent.ADVICE_REQUEST: (
        "посоветуйте",
        "подскажите",
        "что посоветуете",
        "что делать",
        "нужен совет",
        "нужна помощь",
        "как лучше",
        "как правильно",
    ),

    Intent.CONSTRUCTIVE_CRITICISM: (
        "лучше бы",
        "можно было",
        "стоит добавить",
        "я бы сделал",
        "не хватает",
        "нужно улучшить",
        "следовало бы",
        "можно улучшить",
    ),

    Intent.GREETING: (
        "привет",
        "здравствуйте",
        "добрый день",
        "добрый вечер",
        "доброго времени",
        "приветствую",
    ),
}


# ============================================================
# SAFETY TOPICS
# ============================================================


SAFE_PIPELINE_KEYWORDS = (
    # Пожары.
    "пожар",
    "горит",
    "горение",
    "возгорание",

    # Тушение.
    "тушить",
    "тушение",
    "чем тушить",
    "как потушить",

    # Электрика.
    "электрика",
    "электричество",
    "проводка",
    "розетка",
    "короткое замыкание",
    "замыкание",

    # Газ.
    "газ",
    "утечка газа",
    "газовая плита",
    "газовый баллон",

    # Эвакуация.
    "эвакуация",
    "эвакуироваться",
    "выход при пожаре",

    # Пожарная безопасность.
    "пожарная безопасность",
    "огнетушитель",
    "дым",
    "задымление",
)


def requires_safe_pipeline(text: str) -> bool:
    """
    Важные вопросы не должны попадать в простой semantic reply.

    Они должны продолжать идти по существующей цепочке:
        FAQ -> SMART -> AI -> безопасный fallback
    """

    normalized = normalize_text(text)

    return any(keyword in normalized for keyword in SAFE_PIPELINE_KEYWORDS)


# ============================================================
# KEYWORD MATCHING
# ============================================================


def find_keyword_matches(
    text: str,
    keywords: Iterable[str],
) -> tuple[str, ...]:

    normalized = normalize_text(text)
    matches: list[str] = []

    for keyword in keywords:
        keyword_normalized = keyword.lower()

        if keyword_normalized in normalized:
            matches.append(keyword)

    return tuple(matches)


def calculate_intent_score(
    text: str,
    intent: Intent,
) -> IntentMatch | None:

    matches = find_keyword_matches(
        text,
        INTENT_KEYWORDS[intent],
    )

    if not matches:
        return None

    score = len(matches)

    # Вопросительный знак усиливает QUESTION.
    if intent == Intent.QUESTION and "?" in text:
        score += 2

    return IntentMatch(
        intent=intent,
        score=score,
        matched_keywords=matches,
    )


# ============================================================
# MAIN CLASSIFIER
# ============================================================


def classify_comment(text: str | None) -> SemanticResult:

    normalized = normalize_text(text or "")

    if not normalized:
        return SemanticResult(
            text="",
            intents=(),
            matches=(),
            route=Route.SKIP,
            confidence=0.0,
            is_short_reaction=True,
            requires_safe_pipeline=False,
        )

    short_reaction = is_short_reaction(normalized)

    # Важные темы имеют приоритет над semantic templates.
    safe_required = requires_safe_pipeline(normalized)

    matches: list[IntentMatch] = []

    for intent in Intent:
        match = calculate_intent_score(
            normalized,
            intent,
        )

        if match is not None:
            matches.append(match)

    matches.sort(
        key=lambda item: item.score,
        reverse=True,
    )

    intents = tuple(
        match.intent
        for match in matches
    )

    total_score = sum(
        match.score
        for match in matches
    )

    confidence = min(
        1.0,
        total_score / 5,
    )

    # --------------------------------------------------------
    # IMPORTANT SAFETY ROUTING
    # --------------------------------------------------------

    if safe_required:
        return SemanticResult(
            text=normalized,
            intents=intents,
            matches=tuple(matches),
            route=Route.SAFE_PIPELINE,
            confidence=confidence,
            is_short_reaction=short_reaction,
            requires_safe_pipeline=True,
        )

    # --------------------------------------------------------
    # SHORT EMOJI REACTIONS
    # --------------------------------------------------------

    if short_reaction:
        return SemanticResult(
            text=normalized,
            intents=intents,
            matches=tuple(matches),
            route=Route.SKIP,
            confidence=confidence,
            is_short_reaction=True,
            requires_safe_pipeline=False,
        )

    # --------------------------------------------------------
    # QUESTIONS / ADVICE
    # --------------------------------------------------------

    if (
        Intent.QUESTION in intents
        or Intent.ADVICE_REQUEST in intents
    ):
        return SemanticResult(
            text=normalized,
            intents=intents,
            matches=tuple(matches),
            route=Route.EXISTING_PIPELINE,
            confidence=confidence,
            is_short_reaction=False,
            requires_safe_pipeline=False,
        )

    # --------------------------------------------------------
    # SEMANTIC COMBINATIONS
    # --------------------------------------------------------

    meaningful_intents = {
        Intent.PRAISE,
        Intent.GRATITUDE,
        Intent.SUPPORT,
        Intent.AGREEMENT,
        Intent.DISAGREEMENT,
        Intent.PERSONAL_EXPERIENCE,
        Intent.PROFESSION,
        Intent.EDUCATION,
        Intent.FIRE_SERVICE,
        Intent.CONSTRUCTION,
        Intent.HUMOR,
        Intent.SURPRISE,
        Intent.CONTINUATION_INTEREST,
        Intent.CONSTRUCTIVE_CRITICISM,
        Intent.GREETING,
    }

    detected_meaningful = meaningful_intents.intersection(intents)

    if detected_meaningful:
        return SemanticResult(
            text=normalized,
            intents=intents,
            matches=tuple(matches),
            route=Route.SEMANTIC_REPLY,
            confidence=confidence,
            is_short_reaction=False,
            requires_safe_pipeline=False,
        )

    return SemanticResult(
        text=normalized,
        intents=intents,
        matches=tuple(matches),
        route=Route.EXISTING_PIPELINE,
        confidence=confidence,
        is_short_reaction=False,
        requires_safe_pipeline=False,
    )


# ============================================================
# SEMANTIC RESPONSE BUILDER
# ============================================================


def build_semantic_reply(
    result: SemanticResult,
) -> str | None:
    """
    Возвращает осмысленный ответ только для semantic reply.

    None означает:
    текущий semantic-модуль не должен отвечать самостоятельно.
    """

    if result.route != Route.SEMANTIC_REPLY:
        return None

    intents = set(result.intents)

    # --------------------------------------------------------
    # PERSONAL EXPERIENCE + EDUCATION + PROFESSION
    # --------------------------------------------------------

    if (
        Intent.PERSONAL_EXPERIENCE in intents
        and Intent.EDUCATION in intents
        and Intent.PROFESSION in intents
    ):
        return (
            "Вот это интересный профессиональный путь 😄 "
            "Получается, учёба и работа привели вас к совсем "
            "неожиданному направлению!"
        )

    # --------------------------------------------------------
    # PERSONAL EXPERIENCE + PROFESSION
    # --------------------------------------------------------

    if (
        Intent.PERSONAL_EXPERIENCE in intents
        and Intent.PROFESSION in intents
    ):
        return (
            "Интересный опыт! 👍 "
            "Всегда интересно читать реальные истории "
            "о профессиональном пути."
        )

    # --------------------------------------------------------
    # PERSONAL EXPERIENCE
    # --------------------------------------------------------

    if Intent.PERSONAL_EXPERIENCE in intents:
        return (
            "Спасибо, что поделились своим опытом! 👍 "
            "Именно такие реальные истории делают обсуждение интереснее."
        )

    # --------------------------------------------------------
    # EDUCATION + PROFESSION
    # --------------------------------------------------------

    if (
        Intent.EDUCATION in intents
        and Intent.PROFESSION in intents
    ):
        return (
            "Интересно, как образование и профессиональный путь "
            "иногда приводят человека в совершенно разные сферы 😄"
        )

    # --------------------------------------------------------
    # FIRE SERVICE
    # --------------------------------------------------------

    if Intent.FIRE_SERVICE in intents:
        return (
            "Пожарная тема всегда объединяет людей с похожим опытом 🚒👍"
        )

    # --------------------------------------------------------
    # CONSTRUCTION
    # --------------------------------------------------------

    if Intent.CONSTRUCTION in intents:
        return (
            "Стройка — это отдельное приключение 😄🏗️ "
            "Каждый новый этап приносит что-нибудь интересное!"
        )

    # --------------------------------------------------------
    # CONTINUATION
    # --------------------------------------------------------

    if Intent.CONTINUATION_INTEREST in intents:
        return (
            "Продолжение обязательно будет 😄 "
            "Самому интересно, куда всё это приведёт!"
        )

    # --------------------------------------------------------
    # HUMOR
    # --------------------------------------------------------

    if Intent.HUMOR in intents:
        return "😄 Без юмора в таких историях точно никуда!"

    # --------------------------------------------------------
    # SURPRISE
    # --------------------------------------------------------

    if Intent.SURPRISE in intents:
        return (
            "Да, иногда и сам удивляюсь, как всё это получается 😄"
        )

    # --------------------------------------------------------
    # PRAISE
    # --------------------------------------------------------

    if Intent.PRAISE in intents:
        return "Спасибо! Очень приятно читать такие комментарии 👍"

    # --------------------------------------------------------
    # GRATITUDE
    # --------------------------------------------------------

    if Intent.GRATITUDE in intents:
        return "Спасибо за обратную связь! 🤝"

    # --------------------------------------------------------
    # SUPPORT
    # --------------------------------------------------------

    if Intent.SUPPORT in intents:
        return "Спасибо за поддержку! 🤝 Очень ценю!"

    # --------------------------------------------------------
    # AGREEMENT
    # --------------------------------------------------------

    if Intent.AGREEMENT in intents:
        return "Вот-вот! 👍 Рад, что вы тоже так считаете."

    # --------------------------------------------------------
    # DISAGREEMENT
    # --------------------------------------------------------

    if Intent.DISAGREEMENT in intents:
        return (
            "Интересная точка зрения 👍 "
            "Такие обсуждения как раз помогают посмотреть "
            "на ситуацию с разных сторон."
        )

    # --------------------------------------------------------
    # CONSTRUCTIVE CRITICISM
    # --------------------------------------------------------

    if Intent.CONSTRUCTIVE_CRITICISM in intents:
        return (
            "Спасибо за конструктивную обратную связь 👍 "
            "Такие комментарии действительно помогают улучшать контент."
        )

    # --------------------------------------------------------
    # GREETING
    # --------------------------------------------------------

    if Intent.GREETING in intents:
        return "Привет! 👋 Рад видеть вас в комментариях!"

    return None


# ============================================================
# INTEGRATION HELPER
# ============================================================


def should_semantic_reply(
    text: str | None,
) -> tuple[SemanticResult, str | None]:
    """
    Главная функция для интеграции в существующий comments.py.

    Использование:

        result, reply = should_semantic_reply(comment.text)

        if result.route == Route.SAFE_PIPELINE:
            # Продолжаем существующий FAQ -> SMART -> AI
            ...

        elif result.route == Route.SEMANTIC_REPLY and reply:
            # Можно использовать semantic reply
            ...

        elif result.route == Route.SKIP:
            # Ничего не отвечаем
            ...

        else:
            # Продолжаем текущий FAST -> SMART -> AI
            ...
    """

    result = classify_comment(text)

    reply = build_semantic_reply(result)

    return result, reply
