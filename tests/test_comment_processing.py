@@
 def patch_runtime(monkeypatch, fake_ai, auto_reply_enabled=True):
@@
     monkeypatch.setattr(comments, "OpenAIService", lambda *args: fake_ai)
+    # ensure get_global_service returns our fake ai used by runtime
+    monkeypatch.setattr(comments, "get_global_service", lambda: fake_ai)
 
