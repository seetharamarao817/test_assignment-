import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, select, update
from typing import Optional
from sqlalchemy.orm import sessionmaker

from schema import (
    ConversationRef,
    OperatorInboxSubscription,
    OperatorStatus,
    GracePeriodAssignment,
    ConversationState,
    OperatorAvailability,
    GraceReason,
    Operator,
    OperatorRole,
)
from priority_engine import calculate_normalized_priority_with_candidates

# =========================
# DATABASE SETUP
# =========================

DATABASE_PATH = os.getenv("DATABASE_PATH", "./inbox_allocation.db")
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


# =========================
# ALLOCATION (AUTO)
# =========================

def allocate_next_conversation(operator_id):
    """
    Allocation process:
    1. Get tenant_id from operator_id
    2. Check operator is AVAILABLE
    3. Get QUEUED conversations matching tenant_id (from subscribed inboxes)
    4. Get most recent 100
    5. Calculate priority scores for all candidates
    6. Sort by priority_score desc, then last_message_at asc
    7. Allocate top one to operator
    8. Update priority_score in DB along with state
    """
    # Trim whitespace
    operator_id = operator_id.strip() if operator_id else None
    print(f"[ALLOCATE] Starting allocation for operator_id: {operator_id}")
    
    if not operator_id:
        print("[ALLOCATE] ERROR: operator_id is empty")
        return None
    
    with SessionLocal() as db:
        print(f"[ALLOCATE] Step 1: Getting operator with id: {operator_id}")
        operator = db.execute(
            select(Operator).where(Operator.id == operator_id)
        ).scalar_one_or_none()
        
        if not operator:
            print(f"[ALLOCATE] ERROR: Operator not found with id: {operator_id}")
            return None
        
        tenant_id = operator.tenant_id
        print(f"[ALLOCATE] Step 1: Found operator, tenant_id: {tenant_id}")
        
        print(f"[ALLOCATE] Step 2: Checking operator status")
        status = db.execute(
            select(OperatorStatus).where(OperatorStatus.operator_id == operator_id)
        ).scalar_one_or_none()

        if not status:
            print(f"[ALLOCATE] ERROR: Operator status not found for operator_id: {operator_id}")
            return None
        
        if status.status != OperatorAvailability.AVAILABLE:
            print(f"[ALLOCATE] ERROR: Operator not AVAILABLE, current status: {status.status}")
            return None
        
        print(f"[ALLOCATE] Step 2: Operator is AVAILABLE")
        
        print(f"[ALLOCATE] Step 3-4: Getting QUEUED conversations for tenant_id: {tenant_id}")
        candidates = db.execute(
            select(ConversationRef)
            .where(
                ConversationRef.state == ConversationState.QUEUED,
                ConversationRef.tenant_id == tenant_id,  
            )
            .order_by(ConversationRef.last_message_at.desc())
            .limit(100)
        ).scalars().all()
        
        print(f"[ALLOCATE] Step 3-4: Found {len(candidates)} QUEUED conversations")
        if not candidates:
            print(f"[ALLOCATE] ERROR: No QUEUED conversations found for tenant_id: {tenant_id}")
            return None
        
        print(f"[ALLOCATE] Step 5: Calculating priority scores for {len(candidates)} candidates")
        for conv in candidates:
            conv.priority_score = calculate_normalized_priority_with_candidates(
                conv, list(candidates), tenant_id
            )
        
        print(f"[ALLOCATE] Step 6: Sorting candidates by priority")
        candidates_sorted = sorted(
            candidates,
            key=lambda c: (-c.priority_score, c.last_message_at)
        )
        
        # Get the top candidate
        top_conv = candidates_sorted[0]
        print(f"[ALLOCATE] Step 6: Top candidate ID: {top_conv.id}, Priority: {top_conv.priority_score}")
        
        conv = db.execute(
            select(ConversationRef)
            .where(
                ConversationRef.id == top_conv.id,
                ConversationRef.state == ConversationState.QUEUED,
            )
        ).scalar_one_or_none()
        
        if not conv:
            print(f"[ALLOCATE] ERROR: Top conversation {top_conv.id} no longer QUEUED or not found")
            return None
        
        print(f"[ALLOCATE] Step 7-8: Allocating conversation {conv.id} to operator {operator_id}")
        conv.state = ConversationState.ALLOCATED
        conv.assigned_operator_id = operator_id
        conv.priority_score = top_conv.priority_score  
        conv.updated_at = datetime.utcnow()
        
        existing_subscription = db.execute(
            select(OperatorInboxSubscription)
            .where(
                OperatorInboxSubscription.operator_id == operator_id,
                OperatorInboxSubscription.inbox_id == conv.inbox_id,
            )
        ).scalar_one_or_none()
        
        if not existing_subscription:
            print(f"[ALLOCATE] Auto-subscribing operator {operator_id} to inbox {conv.inbox_id}")
            db.add(OperatorInboxSubscription(
                operator_id=operator_id,
                inbox_id=conv.inbox_id,
            ))
        
        db.commit()
        db.refresh(conv)
        db.expunge(conv)
        print(f"[ALLOCATE] SUCCESS: Allocated conversation {conv.id}")
        return conv


# =========================
# LIST QUEUED CONVERSATIONS (for operator)
# =========================

def is_admin(operator_id: str) -> bool:
    """Check if operator is ADMIN."""
    role = get_operator_role(operator_id)
    return role == OperatorRole.ADMIN if role else False


def list_queued_conversations_for_operator(operator_id: str):
    """
    List conversations process (like auto_allocate but returns list):
    1. Get tenant_id from operator_id
    2. Check operator is AVAILABLE
    3. Get QUEUED conversations matching tenant_id
    4. Get most recent 100
    5. Calculate priority scores for all candidates
    6. Sort by priority_score desc, then last_message_at asc
    7. Return list (without allocating)
    """
    # Trim whitespace
    operator_id = operator_id.strip() if operator_id else None
    print(f"[LIST] Starting list for operator_id: {operator_id}")
    
    if not operator_id:
        print("[LIST] ERROR: operator_id is empty")
        return []
    
    with SessionLocal() as db:
        # Step 1: Get tenant_id from operator_id
        print(f"[LIST] Step 1: Getting operator with id: {operator_id}")
        operator = db.execute(
            select(Operator).where(Operator.id == operator_id)
        ).scalar_one_or_none()
        
        if not operator:
            print(f"[LIST] ERROR: Operator not found with id: {operator_id}")
            return []
        
        tenant_id = operator.tenant_id
        print(f"[LIST] Step 1: Found operator, tenant_id: {tenant_id}")
        
        # Step 2: Check operator status (but continue even if OFFLINE)
        print(f"[LIST] Step 2: Checking operator status")
        status = db.execute(
            select(OperatorStatus).where(OperatorStatus.operator_id == operator_id)
        ).scalar_one_or_none()

        if not status:
            print(f"[LIST] WARNING: Operator status not found for operator_id: {operator_id}, continuing anyway")
        else:
            print(f"[LIST] Step 2: Operator status: {status.status}")
            if status.status != OperatorAvailability.AVAILABLE:
                print(f"[LIST] WARNING: Operator is {status.status}, but continuing to show conversations")
        
        # Step 3 & 4: Get QUEUED conversations matching tenant_id, get most recent 100
        print(f"[LIST] Step 3-4: Getting QUEUED conversations for tenant_id: {tenant_id}")
        candidates = db.execute(
            select(ConversationRef)
            .where(
                ConversationRef.state == ConversationState.QUEUED,
                ConversationRef.tenant_id == tenant_id,  # Filter by tenant_id only
            )
            .order_by(ConversationRef.last_message_at.desc())
            .limit(100)
        ).scalars().all()
        
        print(f"[LIST] Step 3-4: Found {len(candidates)} QUEUED conversations")
        if not candidates:
            print(f"[LIST] No QUEUED conversations found for tenant_id: {tenant_id}")
            return []
        
        # Step 5: Calculate priority scores for all candidates
        print(f"[LIST] Step 5: Calculating priority scores for {len(candidates)} candidates")
        for conv in candidates:
            conv.priority_score = calculate_normalized_priority_with_candidates(
                conv, list(candidates), tenant_id
            )
        
        # Step 6: Sort by priority_score desc, then last_message_at asc
        print(f"[LIST] Step 6: Sorting candidates by priority")
        candidates_sorted = sorted(
            candidates,
            key=lambda c: (-c.priority_score, c.last_message_at)
        )
        
        # Update priority scores in database
        print(f"[LIST] Step 7: Updating priority scores in database")
        for conv in candidates_sorted:
            db.execute(
                update(ConversationRef)
                .where(ConversationRef.id == conv.id)
                .values(priority_score=conv.priority_score)
            )
        
        db.commit()
        
        # Refresh all objects to ensure all attributes are loaded, then expunge
        for conv in candidates_sorted:
            db.refresh(conv)  # Load all attributes
            db.expunge(conv)  # Detach from session
        
        # Get operator status for response
        operator_status = status.status.value if status else "UNKNOWN"
        
        print(f"[LIST] SUCCESS: Returning {len(candidates_sorted)} conversations, operator_status: {operator_status}")
        return candidates_sorted, operator_status


# =========================
# MANUAL CLAIM
# =========================

def claim_conversation(conversation_id, operator_id):
    """
    Manual claim process:
    1. Get operator and conversation
    2. Check operator tenant_id matches conversation tenant_id
    3. Check operator is AVAILABLE
    4. Check conversation is QUEUED
    5. Allocate conversation to operator
    6. Check/add inbox subscription
    """
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    operator_id = operator_id.strip() if operator_id else None
    
    print(f"[CLAIM] Starting claim - conversation_id: {conversation_id}, operator_id: {operator_id}")
    
    if not conversation_id or not operator_id:
        print(f"[CLAIM] ERROR: Missing conversation_id or operator_id")
        return None
    
    with SessionLocal() as db:
        # Get operator
        print(f"[CLAIM] Step 1: Getting operator with id: {operator_id}")
        operator = db.execute(
            select(Operator).where(Operator.id == operator_id)
        ).scalar_one_or_none()
        
        if not operator:
            print(f"[CLAIM] ERROR: Operator not found with id: {operator_id}")
            return None
        
        print(f"[CLAIM] Step 1: Found operator, tenant_id: {operator.tenant_id}")
        
        # Get conversation
        print(f"[CLAIM] Step 2: Getting conversation with id: {conversation_id}")
        conv = db.execute(
            select(ConversationRef)
            .where(ConversationRef.id == conversation_id)
        ).scalar_one_or_none()

        if not conv:
            print(f"[CLAIM] ERROR: Conversation not found with id: {conversation_id}")
            return None
        
        print(f"[CLAIM] Step 2: Found conversation, state: {conv.state}, tenant_id: {conv.tenant_id}")
        
        # Check conversation is QUEUED
        if conv.state != ConversationState.QUEUED:
            print(f"[CLAIM] ERROR: Conversation not QUEUED, current state: {conv.state}")
            return None
        
        # Check operator tenant_id matches conversation tenant_id
        if operator.tenant_id != conv.tenant_id:
            print(f"[CLAIM] ERROR: Tenant mismatch - operator tenant: {operator.tenant_id}, conversation tenant: {conv.tenant_id}")
            return None
        
        # Check operator is AVAILABLE
        print(f"[CLAIM] Step 3: Checking operator status")
        status = db.execute(
            select(OperatorStatus).where(OperatorStatus.operator_id == operator_id)
        ).scalar_one_or_none()
        
        if not status:
            print(f"[CLAIM] ERROR: Operator status not found for operator_id: {operator_id}")
            return None
        
        if status.status != OperatorAvailability.AVAILABLE:
            print(f"[CLAIM] ERROR: Operator not AVAILABLE, current status: {status.status}")
            return None
        
        print(f"[CLAIM] Step 3: Operator is AVAILABLE")
        
        # Allocate conversation
        print(f"[CLAIM] Step 4: Allocating conversation {conv.id} to operator {operator_id}")
        conv.state = ConversationState.ALLOCATED
        conv.assigned_operator_id = operator_id
        conv.updated_at = datetime.utcnow()
        
        # Check if operator is subscribed to this inbox, if not add subscription
        existing_subscription = db.execute(
            select(OperatorInboxSubscription)
            .where(
                OperatorInboxSubscription.operator_id == operator_id,
                OperatorInboxSubscription.inbox_id == conv.inbox_id,
            )
        ).scalar_one_or_none()
        
        if not existing_subscription:
            # Auto-subscribe operator to inbox when claiming conversation
            print(f"[CLAIM] Auto-subscribing operator {operator_id} to inbox {conv.inbox_id}")
            db.add(OperatorInboxSubscription(
                operator_id=operator_id,
                inbox_id=conv.inbox_id,
            ))
        
        db.commit()
        db.refresh(conv)
        db.expunge(conv)
        print(f"[CLAIM] SUCCESS: Claimed conversation {conv.id}")
        return conv


# =========================
# RESOLVE
# =========================

def resolve_conversation(conversation_id, operator_id):
    """
    Resolve process:
    1. Get operator and conversation
    2. Check if operator is owner (assigned_operator_id matches)
    3. OR check if operator is manager/admin of same tenant
    4. Resolve conversation
    """
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    operator_id = operator_id.strip() if operator_id else None
    
    print(f"[RESOLVE] Starting resolve - conversation_id: {conversation_id}, operator_id: {operator_id}")
    
    if not conversation_id or not operator_id:
        print(f"[RESOLVE] ERROR: Missing conversation_id or operator_id")
        return None
    
    with SessionLocal() as db:
        # Get operator
        print(f"[RESOLVE] Step 1: Getting operator with id: {operator_id}")
        operator = db.execute(
            select(Operator).where(Operator.id == operator_id)
        ).scalar_one_or_none()
        
        if not operator:
            print(f"[RESOLVE] ERROR: Operator not found with id: {operator_id}")
            return None
        
        print(f"[RESOLVE] Step 1: Found operator, tenant_id: {operator.tenant_id}, role: {operator.role}")
        
        # Get conversation
        print(f"[RESOLVE] Step 2: Getting conversation with id: {conversation_id}")
        conv = db.execute(
            select(ConversationRef)
            .where(ConversationRef.id == conversation_id)
        ).scalar_one_or_none()

        if not conv:
            print(f"[RESOLVE] ERROR: Conversation not found with id: {conversation_id}")
            return None

        print(f"[RESOLVE] Step 2: Found conversation, state: {conv.state}, tenant_id: {conv.tenant_id}, assigned_operator_id: {conv.assigned_operator_id}")

        if conv.state == ConversationState.RESOLVED:
            print(f"[RESOLVE] Conversation already RESOLVED")
            db.refresh(conv)
            db.expunge(conv)
            return conv  # idempotent
        
        # Check if operator is owner
        is_owner = conv.assigned_operator_id == operator_id
        print(f"[RESOLVE] Step 3: Is owner: {is_owner}")
        
        # Check if operator is manager/admin of same tenant
        is_manager_or_admin_same_tenant = (
            operator.tenant_id == conv.tenant_id and
            operator.role in (OperatorRole.MANAGER, OperatorRole.ADMIN)
        )
        print(f"[RESOLVE] Step 3: Is manager/admin same tenant: {is_manager_or_admin_same_tenant}")
        
        # Allow resolve if owner OR (manager/admin of same tenant) OR explicit is_manager flag
        if not is_owner and not is_manager_or_admin_same_tenant:
            print(f"[RESOLVE] ERROR: Permission denied - not owner and not manager/admin of same tenant")
            raise PermissionError("Not allowed to resolve - must be owner or manager/admin of same tenant")

        print(f"[RESOLVE] Step 4: Resolving conversation {conv.id}")
        conv.state = ConversationState.RESOLVED
        conv.resolved_at = datetime.utcnow()
        conv.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(conv)
        db.expunge(conv)
        print(f"[RESOLVE] SUCCESS: Resolved conversation {conv.id}")
        return conv


# =========================
# DEALLOCATE
# =========================

def deallocate_conversation(conversation_id):
    """
    Returns ALLOCATED → QUEUED
    """
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    
    print(f"[DEALLOCATE] Starting deallocate - conversation_id: {conversation_id}")
    
    if not conversation_id:
        print(f"[DEALLOCATE] ERROR: conversation_id is empty")
        return None
    
    with SessionLocal() as db:
        print(f"[DEALLOCATE] Step 1: Getting conversation with id: {conversation_id}")
        conv = db.execute(
            select(ConversationRef)
            .where(ConversationRef.id == conversation_id)
        ).scalar_one_or_none()

        if not conv:
            print(f"[DEALLOCATE] ERROR: Conversation not found with id: {conversation_id}")
            return None
        
        print(f"[DEALLOCATE] Step 1: Found conversation, state: {conv.state}, assigned_operator_id: {conv.assigned_operator_id}")
        
        if conv.state != ConversationState.ALLOCATED:
            print(f"[DEALLOCATE] ERROR: Conversation not ALLOCATED, current state: {conv.state}")
            return None
         
        print(f"[DEALLOCATE] Step 2: Deallocating conversation {conv.id}")
        conv.state = ConversationState.QUEUED
        conv.assigned_operator_id = None
        conv.updated_at = datetime.utcnow()
        
        # Commit the changes
        db.commit()
        
        # Refresh to ensure all attributes are loaded
        db.refresh(conv)
        
        # Expunge to detach from session so it can be used outside
        db.expunge(conv)
        
        print(f"[DEALLOCATE] SUCCESS: Deallocated conversation {conv.id}")
        return conv


# =========================
# OPERATOR OFFLINE → GRACE
# =========================

def operator_goes_offline(operator_id, grace_minutes=1):
    """
    Creates GracePeriodAssignment entries.
    """
    expires = datetime.utcnow() + timedelta(minutes=grace_minutes)

    with SessionLocal.begin() as db:
        db.execute(
            update(OperatorStatus)
            .where(OperatorStatus.operator_id == operator_id)
            .values(
                status=OperatorAvailability.OFFLINE,
                last_status_change_at=datetime.utcnow(),
            )
        )

        conversations = db.execute(
            select(ConversationRef)
            .where(
                ConversationRef.assigned_operator_id == operator_id,
                ConversationRef.state == ConversationState.ALLOCATED,
            )
        ).scalars().all()

        for conv in conversations:
            db.add(
                GracePeriodAssignment(
                    conversation_id=conv.id,
                    operator_id=operator_id,
                    expires_at=expires,
                    reason=GraceReason.OFFLINE,
                )
            )


# =========================
# GRACE EXPIRY JOB
# =========================

def process_grace_expiry():
    """
    Scheduled job:
    - Finds expired grace entries
    - Deallocates conversations
    """
    now = datetime.utcnow()

    with SessionLocal.begin() as db:
        expired = db.execute(
            select(GracePeriodAssignment)
            .where(GracePeriodAssignment.expires_at <= now)
        ).scalars().all()

        for grace in expired:
            conv = db.execute(
                select(ConversationRef)
                .where(ConversationRef.id == grace.conversation_id)
            ).scalar_one_or_none()

            if conv and conv.state == ConversationState.ALLOCATED:
                conv.state = ConversationState.QUEUED
                conv.assigned_operator_id = None
                conv.updated_at = now

            db.delete(grace)



def operator_goes_online(operator_id):
    """
    Updates OperatorStatus to AVAILABLE and removes all grace assignments.
    Conversations remain in ALLOCATED state.
    """
    with SessionLocal.begin() as db:
        # Update status to AVAILABLE
        db.execute(
            update(OperatorStatus)
            .where(OperatorStatus.operator_id == operator_id)
            .values(
                status=OperatorAvailability.AVAILABLE,
                last_status_change_at=datetime.utcnow(),
            )
        )
        
        # Remove all grace assignments for this operator
        grace_assignments = db.execute(
            select(GracePeriodAssignment)
            .where(GracePeriodAssignment.operator_id == operator_id)
        ).scalars().all()
        
        for grace in grace_assignments:
            db.delete(grace)

    return True






def get_operator_status(operator_id):
    """
    Returns OperatorStatus.
    """
    if not operator_id:
        return None
    
    operator_id = operator_id.strip()
    
    with SessionLocal() as db:
        status = db.execute(
            select(OperatorStatus)
            .where(OperatorStatus.operator_id == operator_id)
        ).scalar_one_or_none()
        
        if status:
            db.refresh(status)
            db.expunge(status)
        
        return status

# =========================
# PERMISSION & ROLE CHECKING
# =========================

def get_operator_role(operator_id: str) -> Optional[OperatorRole]:
    """Get operator role."""
    if not operator_id:
        return None
    
    # Trim whitespace from operator_id
    operator_id = operator_id.strip()
    
    with SessionLocal() as db:
        operator = db.execute(
            select(Operator).where(Operator.id == operator_id)
        ).scalar_one_or_none()
        
        return operator.role if operator else None


def is_manager_or_admin(operator_id: str) -> bool:
    """Check if operator is MANAGER or ADMIN."""
    if not operator_id:
        return False
    
    role = get_operator_role(operator_id)
    
    return role in (OperatorRole.MANAGER, OperatorRole.ADMIN) if role else False


def owns_conversation(operator_id: str, conversation_id: str) -> bool:
    """Check if operator owns the conversation."""
    with SessionLocal() as db:
        conv = db.execute(
            select(ConversationRef)
            .where(ConversationRef.id == conversation_id)
        ).scalar_one_or_none()
        return conv and conv.assigned_operator_id == operator_id


def is_subscribed_to_inbox(operator_id: str, inbox_id: str) -> bool:
    """Check if operator is subscribed to inbox."""
    with SessionLocal() as db:
        subscription = db.execute(
            select(OperatorInboxSubscription)
            .where(
                OperatorInboxSubscription.operator_id == operator_id,
                OperatorInboxSubscription.inbox_id == inbox_id,
            )
        ).scalar_one_or_none()
        return subscription is not None


def can_resolve_conversation(operator_id: str, conversation_id: str) -> bool:
    """Check if operator can resolve (owner, manager, or admin)."""
    if owns_conversation(operator_id, conversation_id):
        return True
    return is_manager_or_admin(operator_id)


def get_operator_inboxes(operator_id):
    """
    Returns list of inboxes operator is subscribed to.
    """
    from schema import Inbox
    
    if not operator_id:
        return []
    
    operator_id = operator_id.strip()
    
    with SessionLocal() as db:
        # Get subscribed inbox IDs
        subscription_ids = db.execute(
            select(OperatorInboxSubscription.inbox_id)
            .where(OperatorInboxSubscription.operator_id == operator_id)
        ).scalars().all()
        
        if not subscription_ids:
            return []
        
        # Get inboxes
        inboxes = db.execute(
            select(Inbox)
            .where(Inbox.id.in_(subscription_ids))
        ).scalars().all()
        
        # Refresh and expunge all inboxes to detach from session
        for inbox in inboxes:
            db.refresh(inbox)
            db.expunge(inbox)
        
        return list(inboxes)

