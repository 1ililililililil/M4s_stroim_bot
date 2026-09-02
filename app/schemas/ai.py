from typing import Literal
from pydantic import BaseModel, Field

Category = Literal[
    "QUESTION","PRAISE","JOKE","DISCUSSION","CRITICISM","NEGATIVE",
    "CONFLICT","SPAM","ADVERTISING","INSULT","OFF_TOPIC","OTHER"
]
Sentiment = Literal["positive","neutral","negative"]

class AIAnalysisResult(BaseModel):
    category: Category = "OTHER"
    sentiment: Sentiment = "neutral"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    requires_admin: bool = True
    should_reply: bool = False


class AIAutoReplyResult(AIAnalysisResult):
    reply: str = ""


class ResponseVariant(BaseModel):
    variant: int = Field(ge=1)
    text: str = Field(min_length=1)

class AIResponsesResult(BaseModel):
    responses: list[ResponseVariant]
