from datetime import datetime
from sqlalchemy import String, Text, Integer, BigInteger, Boolean, Float, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (UniqueConstraint("chat_id", "telegram_message_id", name="uq_comment_message"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    post_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    post_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processing: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_reply_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    published_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)

class AIAnalysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), unique=True)
    category: Mapped[str] = mapped_column(String(32))
    sentiment: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text)
    requires_admin: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class AIResponse(Base):
    __tablename__ = "ai_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), index=True)
    variant_number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class AdminAction(Base):
    __tablename__ = "admin_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), index=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    selected_variant: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class BotSettings(Base):
    __tablename__ = "bot_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_reply_threshold: Mapped[float] = mapped_column(Float, default=0.95)

class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    __table_args__ = (
        Index("ix_knowledge_source", "chat_id", "source_message_id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="GENERAL")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    answer_variants: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UnansweredQuestion(Base):
    __tablename__ = "unanswered_questions"
    __table_args__ = (
        UniqueConstraint("comment_id", name="uq_unanswered_comment"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), unique=True)
    question_text: Mapped[str] = mapped_column(Text)
    post_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="NEW")
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AnswerCandidate(Base):
    __tablename__ = "answer_candidates"
    __table_args__ = (
        UniqueConstraint("comment_id", name="uq_answer_candidate_comment"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), unique=True)
    question_text: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="GENERAL")
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
