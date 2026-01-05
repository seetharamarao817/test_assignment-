import os
import uuid
from datetime import datetime
from typing import Optional, List, Tuple
from sqlalchemy import create_engine, and_, or_, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError


from schema import (
    Base,
    Inbox,
    Operator,
    OperatorInboxSubscription,
    ConversationRef,
    Label,
    ConversationLabel,
    OperatorStatus,
    GracePeriodAssignment,
    OperatorRole,
    ConversationState,
    OperatorAvailability,
    GraceReason,
    Tenant,
)

# =========================
# DATABASE SETUP
# =========================

DATABASE_PATH = os.getenv("DATABASE_PATH", "./inbox_allocation.db")
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


# =========================
# INSERT FUNCTIONS
# =========================


def get_or_create_inbox(tenant_id, phone_number, display_name):
    with SessionLocal() as db:
        inbox = (
            db.query(Inbox)
            .filter(
                Inbox.tenant_id == tenant_id,
                Inbox.phone_number == phone_number,
            )
            .first()
        )

        if inbox:
            return inbox

        inbox = Inbox(
            tenant_id=tenant_id,
            phone_number=phone_number,
            display_name=display_name,
        )

        db.add(inbox)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            inbox = (
                db.query(Inbox)
                .filter(
                    Inbox.tenant_id == tenant_id,
                    Inbox.phone_number == phone_number,
                )
                .first()
            )

        db.refresh(inbox)
        db.expunge(inbox)
        return inbox



def create_operator(tenant_id, role: OperatorRole):
    with SessionLocal() as db:
        operator = Operator(
            tenant_id=tenant_id,
            role=role,
        )
        db.add(operator)
        db.commit()
        db.refresh(operator)
        db.expunge(operator)

    with SessionLocal() as db:
        op_status = OperatorStatus(
            operator_id=operator.id,
            status="OFFLINE",
        )
        db.add(op_status)
        db.commit()
        return operator


def subscribe_operator_to_inbox(operator_id, inbox_id):
    with SessionLocal() as db:
        sub = OperatorInboxSubscription(
            operator_id=operator_id,
            inbox_id=inbox_id,
        )
        db.add(sub)
        db.commit()
        return sub


def set_operator_status(operator_id, status: OperatorAvailability):
    with SessionLocal() as db:
        op_status = OperatorStatus(
            operator_id=operator_id,
            status=status,
        )
        db.add(op_status)
        db.commit()
        return op_status




def create_or_update_conversation(
    tenant_id,
    inbox_id,
    external_conversation_id,
    customer_phone_number,
):
    """
    Create or update conversation.
    Priority score is NOT calculated here - it's set to 0.0.
    Priority is calculated only during allocation.
    """
    now = datetime.utcnow()

    with SessionLocal() as db:
        conv = (
            db.query(ConversationRef)
            .filter(
                and_(
                    ConversationRef.tenant_id == tenant_id,
                    ConversationRef.inbox_id == inbox_id,
                    ConversationRef.external_conversation_id == external_conversation_id,
                )
            )
            .one_or_none()
        )

        # 🆕 Create if not exists
        if conv is None:
            conv = ConversationRef(
                tenant_id=tenant_id,
                inbox_id=inbox_id,
                external_conversation_id=external_conversation_id,
                customer_phone_number=customer_phone_number,
                state=ConversationState.QUEUED,
                last_message_at=now,
                message_count=1,
                priority_score=0.0,  # Priority calculated during allocation only
            )
            db.add(conv)

        # 🔄 Update if exists
        else:
            conv.message_count += 1
            conv.last_message_at = now
            conv.updated_at = now
            # Priority remains unchanged - will be recalculated during allocation

        db.commit()
        db.refresh(conv)
        db.expunge(conv)
        return conv



def create_label(tenant_id, inbox_id, name, created_by, color=None):
    with SessionLocal() as db:
        label = Label(
            tenant_id=tenant_id,
            inbox_id=inbox_id,
            name=name,
            color=color,
            created_by=created_by,
        )
        db.add(label)
        db.commit()
        db.refresh(label)
        db.expunge(label)
        return label


def attach_label_to_conversation(conversation_id, label_id):
    with SessionLocal() as db:
        mapping = ConversationLabel(
            conversation_id=conversation_id,
            label_id=label_id,
        )
        db.add(mapping)
        db.commit()
        db.refresh(mapping)
        db.expunge(mapping)
        return mapping


def create_grace_assignment(conversation_id, operator_id, expires_at):
    with SessionLocal() as db:
        grace = GracePeriodAssignment(
            conversation_id=conversation_id,
            operator_id=operator_id,
            expires_at=expires_at,
            reason=GraceReason.OFFLINE,
        )
        db.add(grace)
        db.commit()
        return grace


# =========================
# TENANT CONFIG OPERATIONS
# =========================

def get_or_create_tenant_config(tenant_id: str, alpha: float = 1.0, beta: float = 1.0) -> Tenant:
    """Get or create tenant config with default weights."""
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.tenant_id == tenant_id
        ).first()
        
        if tenant:
            return tenant
        
        tenant = Tenant(
            tenant_id=tenant_id,
            alpha=alpha,
            beta=beta,
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        db.expunge(tenant)
        return tenant


def get_tenant_config(tenant_id: str) -> Optional[Tenant]:
    """Get tenant config."""
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.tenant_id == tenant_id
        ).first()
        if tenant:
            db.refresh(tenant)
            db.expunge(tenant)
        return tenant


def update_tenant_config(
    tenant_id: str,
    alpha: Optional[float] = None,
    beta: Optional[float] = None,
) -> Tenant:
    """
    Create tenant config if not exists, otherwise update alpha/beta.
    Only ADMIN can call this (enforce at API/service layer).
    """
    with SessionLocal() as db:
        tenant = (
            db.query(Tenant)
            .filter(Tenant.tenant_id == tenant_id)
            .one_or_none()
        )

        # 🔹 Case 1: Tenant does not exist → CREATE
        if tenant is None:
            tenant = Tenant(
                tenant_id=tenant_id,
                alpha=alpha if alpha is not None else 1.0,
                beta=beta if beta is not None else 1.0,
            )
            db.add(tenant)

        # 🔹 Case 2: Tenant exists → UPDATE
        else:
            if alpha is not None:
                tenant.alpha = alpha
            if beta is not None:
                tenant.beta = beta
            tenant.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(tenant)
        db.expunge(tenant)
        return tenant



# =========================
# CONVERSATION LISTING & FILTERING
# =========================

def list_conversations(
    operator_id: str,
    inbox_id: Optional[str] = None,
    state: Optional[ConversationState] = None,
    assigned_operator_id: Optional[str] = None,
) -> Tuple[List[ConversationRef], int]:

    with SessionLocal() as db:
        operator = db.query(Operator).filter(Operator.id == operator_id).first()
        if not operator:
            return [], 0

        tenant_id = operator.tenant_id

        query = db.query(ConversationRef).filter(
            ConversationRef.tenant_id == tenant_id
        )

        # Apply filters
        if state:
            query = query.filter(ConversationRef.state == state)
        if inbox_id:
            query = query.filter(ConversationRef.inbox_id == inbox_id)
        if assigned_operator_id:
            query = query.filter(
                ConversationRef.assigned_operator_id == assigned_operator_id
            )

        # FINAL query: last 100 conversations ONLY
        conversations = (
            query
            .order_by(ConversationRef.last_message_at.desc())
            .limit(100)
            .all()
        )

        total_count = len(conversations)

        return conversations, total_count




def reassign_conversation(conversation_id: str, target_operator_id: str, operator_id: str) -> Optional[ConversationRef]:
    """
    Reassign conversation to another operator.
    Only allowed for MANAGER or ADMIN.
    """
    from allocation_engine import is_manager_or_admin
    
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    target_operator_id = target_operator_id.strip() if target_operator_id else None
    operator_id = operator_id.strip() if operator_id else None
    
    print(f"[REASSIGN] Starting reassign - conversation_id: {conversation_id}, target_operator_id: {target_operator_id}, operator_id: {operator_id}")
    
    if not conversation_id or not target_operator_id or not operator_id:
        print(f"[REASSIGN] ERROR: Missing required parameters")
        return None
    
    if not is_manager_or_admin(operator_id):
        print(f"[REASSIGN] ERROR: Operator {operator_id} is not MANAGER or ADMIN")
        raise PermissionError("Only MANAGER or ADMIN can reassign conversations")
    
    print(f"[REASSIGN] Step 1: Permission check passed")
    
    with SessionLocal() as db:
        print(f"[REASSIGN] Step 2: Getting conversation with id: {conversation_id}")
        conv = db.query(ConversationRef).filter(
            ConversationRef.id == conversation_id
        ).first()
        
        if not conv:
            print(f"[REASSIGN] ERROR: Conversation not found with id: {conversation_id}")
            return None
        
        print(f"[REASSIGN] Step 2: Found conversation, state: {conv.state}, assigned_operator_id: {conv.assigned_operator_id}")
        
        # Reassign works for RESOLVED or QUEUED, but NOT for ALLOCATED (must deallocate first)
        if conv.state == ConversationState.ALLOCATED:
            print(f"[REASSIGN] ERROR: Conversation is ALLOCATED. Must deallocate first before reassigning. Current state: {conv.state}")
            return None
        
        if conv.state not in (ConversationState.RESOLVED, ConversationState.QUEUED):
            print(f"[REASSIGN] ERROR: Conversation state not allowed for reassign. Current state: {conv.state}")
            return None
        
        # Verify target operator exists and is in same tenant
        from schema import Operator
        target_operator = db.query(Operator).filter(Operator.id == target_operator_id).first()
        if not target_operator:
            print(f"[REASSIGN] ERROR: Target operator not found with id: {target_operator_id}")
            return None
        
        if target_operator.tenant_id != conv.tenant_id:
            print(f"[REASSIGN] ERROR: Tenant mismatch - conversation tenant: {conv.tenant_id}, target operator tenant: {target_operator.tenant_id}")
            return None
        
        print(f"[REASSIGN] Step 3: Reassigning conversation {conv.id} from {conv.assigned_operator_id} to {target_operator_id}")
        conv.assigned_operator_id = target_operator_id
        conv.updated_at = datetime.utcnow()
        conv.state = ConversationState.ALLOCATED
        db.commit()
        db.refresh(conv)
        db.expunge(conv)
        print(f"[REASSIGN] SUCCESS: Reassigned conversation {conv.id}")
        return conv


def move_conversation_inbox(conversation_id: str, target_inbox_id: str, operator_id: str) -> Optional[ConversationRef]:
    """
    Move conversation to another inbox in same tenant.
    Only allowed for MANAGER or ADMIN.
    """
    from allocation_engine import is_manager_or_admin
    
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    target_inbox_id = target_inbox_id.strip() if target_inbox_id else None
    operator_id = operator_id.strip() if operator_id else None
    
    print(f"[MOVE_INBOX] Starting move - conversation_id: {conversation_id}, target_inbox_id: {target_inbox_id}, operator_id: {operator_id}")
    
    if not conversation_id or not target_inbox_id or not operator_id:
        print(f"[MOVE_INBOX] ERROR: Missing required parameters")
        return None
    
    if not is_manager_or_admin(operator_id):
        print(f"[MOVE_INBOX] ERROR: Operator {operator_id} is not MANAGER or ADMIN")
        raise PermissionError("Only MANAGER or ADMIN can move conversations")
    
    print(f"[MOVE_INBOX] Step 1: Permission check passed")
    
    with SessionLocal() as db:
        print(f"[MOVE_INBOX] Step 2: Getting conversation with id: {conversation_id}")
        conv = db.query(ConversationRef).filter(
            ConversationRef.id == conversation_id
        ).first()
        
        if not conv:
            print(f"[MOVE_INBOX] ERROR: Conversation not found with id: {conversation_id}")
            return None
        
        print(f"[MOVE_INBOX] Step 2: Found conversation, current inbox_id: {conv.inbox_id}, tenant_id: {conv.tenant_id}")
        
        # Verify target inbox is in same tenant
        print(f"[MOVE_INBOX] Step 3: Verifying target inbox {target_inbox_id}")
        target_inbox = db.query(Inbox).filter(Inbox.id == target_inbox_id).first()
        if not target_inbox:
            print(f"[MOVE_INBOX] ERROR: Target inbox not found with id: {target_inbox_id}")
            raise ValueError("Target inbox not found")
        
        if target_inbox.tenant_id != conv.tenant_id:
            print(f"[MOVE_INBOX] ERROR: Tenant mismatch - conversation tenant: {conv.tenant_id}, target inbox tenant: {target_inbox.tenant_id}")
            raise ValueError("Target inbox must be in the same tenant")
        
        print(f"[MOVE_INBOX] Step 4: Moving conversation {conv.id} from inbox {conv.inbox_id} to {target_inbox_id}")
        conv.inbox_id = target_inbox_id
        conv.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(conv)
        db.expunge(conv)
        print(f"[MOVE_INBOX] SUCCESS: Moved conversation {conv.id} to inbox {target_inbox_id}")
        return conv


def search_conversations_by_phone(tenant_id: str, phone_number: str) -> List[ConversationRef]:
    """Exact match search by phone number."""
    with SessionLocal() as db:
        conversations = db.query(ConversationRef).filter(
            ConversationRef.tenant_id == tenant_id,
            ConversationRef.customer_phone_number == phone_number
        ).all()
        
        # Refresh and expunge all conversations to detach from session
        for conv in conversations:
            db.refresh(conv)
            db.expunge(conv)
        
        return list(conversations)


# =========================
# LABEL OPERATIONS
# =========================

def list_inbox_labels(inbox_id: str) -> List[Label]:
    """List all labels for an inbox."""
    with SessionLocal() as db:
        labels = db.query(Label).filter(Label.inbox_id == inbox_id).all()
        
        # Refresh and expunge all labels to detach from session
        for label in labels:
            db.refresh(label)
            db.expunge(label)
        
        return list(labels)


def update_label(label_id: str, name: Optional[str] = None, color: Optional[str] = None) -> Optional[Label]:
    """Update label."""
    with SessionLocal() as db:
        label = db.query(Label).filter(Label.id == label_id).first()
        if not label:
            return None
        
        if name is not None:
            label.name = name
        if color is not None:
            label.color = color
        
        db.commit()
        db.refresh(label)
        db.expunge(label)
        return label


def delete_label(label_id: str) -> bool:
    """Delete label and all conversation associations."""
    with SessionLocal() as db:
        # Delete conversation associations
        db.query(ConversationLabel).filter(
            ConversationLabel.label_id == label_id
        ).delete()
        
        # Delete label
        deleted = db.query(Label).filter(Label.id == label_id).delete()
        db.commit()
        return deleted > 0


def get_conversation_labels(conversation_id: str) -> List[Label]:
    """Get all labels attached to conversation."""
    with SessionLocal() as db:
        labels = db.query(Label).join(ConversationLabel).filter(
            ConversationLabel.conversation_id == conversation_id
        ).all()
        
        # Refresh and expunge all labels to detach from session
        for label in labels:
            db.refresh(label)
            db.expunge(label)
        
        return list(labels)


def detach_label_from_conversation(conversation_id: str, label_id: str) -> bool:
    """Remove label from conversation."""
    with SessionLocal() as db:
        deleted = db.query(ConversationLabel).filter(
            ConversationLabel.conversation_id == conversation_id,
            ConversationLabel.label_id == label_id
        ).delete()
        db.commit()
        return deleted > 0





