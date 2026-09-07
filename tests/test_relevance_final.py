from app.services.openai_service import is_response_relevant


def test_negative_experience_rejected():
    comment = "Я не люблю массовые мероприятия"
    response = (
        "Зато опыт никуда не пропадает, "
        "даже если работаешь уже совсем в другой сфере 👍"
    )

    assert not is_response_relevant(response, comment, "")


def test_negative_profession_rejected():
    comment = "Я не люблю массовые мероприятия"
    response = "Понимаю 😊 Главное развиваться в профессии."

    assert not is_response_relevant(response, comment, "")


def test_negative_emotion_only_rejected():
    comment = "Я не люблю массовые мероприятия"
    response = "Понимаю"

    assert not is_response_relevant(response, comment, "")


def test_positive_paraphrase_many_people():
    comment = "Я не люблю массовые мероприятия"
    response = "Понимаю 😊 Не всем комфортно, когда вокруг много людей."

    assert is_response_relevant(response, comment, "")


def test_positive_paraphrase_small_atmosphere():
    comment = "Я не люблю массовые мероприятия"
    response = "Тоже понимаю 😄 Иногда камерная атмосфера намного приятнее."

    assert is_response_relevant(response, comment, "")


def test_positive_paraphrase_crowds():
    comment = "Я не люблю массовые мероприятия"
    response = "Да, большие скопления людей нравятся далеко не всем 😊"

    assert is_response_relevant(response, comment, "")


def test_context_does_not_replace_comment_topic():
    comment = "Я не люблю массовые мероприятия"
    context = "В обсуждении также говорилось о профессии и обучении"
    response = "Понимаю 😊 Главное развиваться в профессии."

    assert not is_response_relevant(response, comment, context)
