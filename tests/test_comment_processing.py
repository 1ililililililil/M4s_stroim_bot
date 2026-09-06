def patch_runtime(monkeypatch, fake_ai, auto_reply_enabled=True):
    # Подменяем глобальный AI-сервис тестовым fake_ai
    monkeypatch.setattr(
        comments,
        "get_global_service",
        lambda: fake_ai,
    )
