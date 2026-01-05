import uuid
import enum
from sqlalchemy import (
    create_engine,
    Column,
    String,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Float,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import CHAR
from sqlalchemy.sql import func


# =========================
# ID GENERATION HELPERS
# =========================

def generate_conversation_id():
    """Generate ID in format: conv-{first5chars}"""
    full_uuid = str(uuid.uuid4())
    return f"conv-{full_uuid[:5]}"


def generate_operator_id():
    """Generate ID in format: op-{first5chars}"""
    full_uuid = str(uuid.uuid4())
    return f"op-{full_uuid[:5]}"


def generate_inbox_id():
    """Generate ID in format: inbox-{first5chars}"""
    full_uuid = str(uuid.uuid4())
    return f"inbox-{full_uuid[:5]}"


# =========================
# DATABASE CONFIG (SQLite)
# =========================

DATABASE_URL = "sqlite:///./inbox_allocation.db"
engine = create_engine(DATABASE_URL, echo=True)


class Base(DeclarativeBase):
    pass


# =========================
# ENUMS
# =========================

class OperatorRole(str, enum.Enum):
    OPERATOR = "OPERATOR"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"


class ConversationState(str, enum.Enum):
    QUEUED = "QUEUED"
    ALLOCATED = "ALLOCATED"
    RESOLVED = "RESOLVED"


class OperatorAvailability(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    OFFLINE = "OFFLINE"


class GraceReason(str, enum.Enum):
    OFFLINE = "OFFLINE"
    MANUAL = "MANUAL"


# =========================
# TABLES
# =========================

class Inbox(Base):
    __tablename__ = "inboxes"

    id = Column(String, primary_key=True, default=generate_inbox_id)
    tenant_id = Column(CHAR(36), nullable=False)
    phone_number = Column(String, nullable=False)
    display_name = Column(String, nullable=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_number", name="uq_tenant_phone"),
    )


class Operator(Base):
    __tablename__ = "operators"

    id = Column(String, primary_key=True, default=generate_operator_id)
    tenant_id = Column(CHAR(36), nullable=False)
    role = Column(Enum(OperatorRole), nullable=False)


class OperatorInboxSubscription(Base):
    __tablename__ = "operator_inbox_subscriptions"

    id = Column(CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operator_id = Column(String, ForeignKey("operators.id"), nullable=False)
    inbox_id = Column(String, ForeignKey("inboxes.id"), nullable=False)


class ConversationRef(Base):
    __tablename__ = "conversation_refs"

    id = Column(String, primary_key=True, default=generate_conversation_id)
    tenant_id = Column(CHAR(36), nullable=False)
    inbox_id = Column(String, ForeignKey("inboxes.id"), nullable=False)

    external_conversation_id = Column(String, nullable=False)
    customer_phone_number = Column(String, nullable=False)

    state = Column(Enum(ConversationState), nullable=False, index=True)
    assigned_operator_id = Column(
        String, ForeignKey("operators.id"), nullable=True
    )

    last_message_at = Column(DateTime, nullable=False)
    message_count = Column(Integer, nullable=False, default=0)
    priority_score = Column(Float, nullable=False, default=0.0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_inbox_state_priority", "inbox_id", "state", "priority_score"),
    )


class Label(Base):
    __tablename__ = "labels"

    id = Column(CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(CHAR(36), nullable=False)
    inbox_id = Column(String, ForeignKey("inboxes.id"), nullable=False)

    name = Column(String, nullable=False)
    color = Column(String, nullable=True)
    created_by = Column(String, ForeignKey("operators.id"), nullable=False)

    created_at = Column(DateTime, server_default=func.now())


class ConversationLabel(Base):
    __tablename__ = "conversation_labels"

    id = Column(CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(
        CHAR(36), ForeignKey("conversation_refs.id"), nullable=False
    )
    label_id = Column(CHAR(36), ForeignKey("labels.id"), nullable=False)

    created_at = Column(DateTime, server_default=func.now())


class OperatorStatus(Base):
    __tablename__ = "operator_status"

    id = Column(CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operator_id = Column(
        String, ForeignKey("operators.id"), nullable=False, unique=True
    )
    status = Column(Enum(OperatorAvailability), nullable=False)
    last_status_change_at = Column(DateTime, server_default=func.now())


class GracePeriodAssignment(Base):
    __tablename__ = "grace_period_assignments"

    id = Column(CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(
        String, ForeignKey("conversation_refs.id"), nullable=False
    )
    operator_id = Column(
        String, ForeignKey("operators.id"), nullable=False
    )
    expires_at = Column(DateTime, nullable=False)
    reason = Column(Enum(GraceReason), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(CHAR(36), nullable=False, unique=True)
    alpha = Column(Float, nullable=False, default=1.0)  # message_count weight
    beta = Column(Float, nullable=False, default=1.0)  # delay weight
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())




# =========================
# CREATE TABLES
# =========================

if __name__ == "__main__":
    Base.metadata.create_all(engine)
    print("✅ SQLite database and tables created successfully")
