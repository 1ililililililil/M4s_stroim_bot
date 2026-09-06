def patch_runtime(monkeypatch, fake_ai, auto_reply_enabled=True):
    # Подменяем глобальный AI-сервис тестовым fake_ai
    monkeypatch.setattr(
        comments,
        "get_global_service",
        lambda: fake_ai,
    )

    # При необходимости подменяем настройки автоответа
    monkeypatch.setattr(
        comments,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "auto_reply_enabled": auto_reply_enabled,
            },
        )(),
    )
