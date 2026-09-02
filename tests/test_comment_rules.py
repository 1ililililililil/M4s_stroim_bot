from types import SimpleNamespace

from app.bot.handlers.comments import (
    MAX_PARENT_COMMENT_CHARS,
    MAX_POST_CONTEXT_CHARS,
    build_post_context,
    has_meaningful_question,
    is_simple_reaction,
    local_social_category,
    normalize_analysis,
    precheck_comment,
)
from app.schemas.ai import AIAnalysisResult


def test_reactions_and_gratitude_do_not_require_reply():
    assert is_simple_reaction("🔥🔥🔥")
    assert is_simple_reaction("Спасибо!")
    assert is_simple_reaction("Благодарю")
    assert is_simple_reaction("Интересно")
    assert not is_simple_reaction("Класс, а как пожарные готовятся к этому?")

    analysis = normalize_analysis(
        AIAnalysisResult(category="PRAISE", should_reply=True, requires_admin=True),
        "Спасибо!",
    )
    assert analysis.should_reply is False
    assert analysis.requires_admin is False

    reaction = precheck_comment("Спасибо!")
    assert reaction is not None
    assert reaction.category == "PRAISE"
    assert reaction.should_reply is False
    assert reaction.requires_admin is False


def test_short_questions_override_reaction_prefixes():
    questions = [
        "Класс?",
        "Спасибо, а что делать дальше?",
        "👍 А где можно узнать подробнее?",
        "Как пожарные этому учатся",
        "Интересно, а почему так происходит?",
    ]
    for text in questions:
        assert has_meaningful_question(text)
        assert not is_simple_reaction(text)
        assert precheck_comment(text) is None


def test_short_reactions_are_prechecked_without_ai():
    for text in ["Спасибо", "🔥", "Класс", "Супер", "Отлично", "Молодцы", "Интересно"]:
        result = precheck_comment(text)
        assert result is not None
        assert result.category == "PRAISE"
        assert result.should_reply is False


def test_meaningful_social_comments_are_local_but_short_ones_can_skip():
    assert local_social_category("Вот это многогранность личности! 😍 Здорово!") == "PRAISE"
    assert local_social_category("Уважаю людей, которые умеют так многое") == "PRAISE"
    assert local_social_category(
        "Не совпало, но близко, училась на секретаря, работаю бухгалтером"
    ) == "DISCUSSION"
    assert local_social_category("Не совпало") is None
    assert local_social_category("Почему люди часто меняют профессию?") is None

    skipped = precheck_comment("Совсем другая сфера")
    assert skipped is not None
    assert skipped.category == "DISCUSSION"
    assert skipped.should_reply is False


def test_build_post_context_from_post_and_parent_comment():
    post = SimpleNamespace(
        message_id=101,
        text="Пожарный самолёт способен доставлять воду в район пожара.",
        caption=None,
        photo=None,
    )
    author = SimpleNamespace(id=42, username="parent_user", first_name="Parent")
    parent = SimpleNamespace(
        message_id=102,
        text="А сколько он поднимает?",
        caption=None,
        from_user=author,
        reply_to_message=post,
    )
    comment = SimpleNamespace(reply_to_message=parent)

    context = build_post_context(comment)

    assert context["post_message_id"] == 101
    assert context["reply_to_message_id"] == 102
    assert "КОНТЕКСТ ПУБЛИКАЦИИ" in context["post_context"]
    assert "Пожарный самолёт" in context["post_context"]
    assert "КОНТЕКСТ РОДИТЕЛЬСКОГО КОММЕНТАРИЯ" in context["post_context"]
    assert "@parent_user" in context["post_context"]
    assert "А сколько он поднимает?" in context["post_context"]


def test_build_post_context_is_bounded_and_handles_missing_context():
    long_post = SimpleNamespace(
        message_id=103,
        text="слово " * 2000,
        caption=None,
        photo=None,
    )
    context = build_post_context(SimpleNamespace(reply_to_message=long_post))
    missing = build_post_context(SimpleNamespace(reply_to_message=None))

    assert len(context["post_context"]) <= (
        MAX_POST_CONTEXT_CHARS + MAX_PARENT_COMMENT_CHARS + 300
    )
    assert missing == {
        "post_message_id": None,
        "reply_to_message_id": None,
        "post_context": None,
    }


def test_precheck_routes_advertising_and_toxicity_to_moderation():
    advertising = precheck_comment("Реклама, купить здесь https://example.com")
    assert advertising is not None
    assert advertising.category in {"ADVERTISING", "SPAM"}
    assert advertising.should_reply is False

    toxic = precheck_comment("Идиот")
    assert toxic is not None
    assert toxic.category == "INSULT"
    assert toxic.should_reply is False