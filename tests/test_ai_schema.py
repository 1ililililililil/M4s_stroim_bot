from app.schemas.ai import AIAnalysisResult

def test_ai_schema():
    x = AIAnalysisResult.model_validate({
        "category":"QUESTION","sentiment":"positive","confidence":0.9,
        "summary":"test","requires_admin":False,"should_reply":True
    })
    assert x.category == "QUESTION"

def test_default_fallback():
    x = AIAnalysisResult()
    assert x.category == "OTHER"
    assert x.requires_admin is True
