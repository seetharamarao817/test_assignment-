import os
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from allocation_engine import (
    allocate_next_conversation,
    claim_conversation,
    resolve_conversation,
    deallocate_conversation,
    operator_goes_offline,
    operator_goes_online,
    get_operator_status,
    get_operator_inboxes,
    is_manager_or_admin,
    list_queued_conversations_for_operator,
    is_admin,
)

from database_operations import (
    create_or_update_conversation,
    get_or_create_inbox,
    reassign_conversation,
    move_conversation_inbox,
    search_conversations_by_phone,
    create_label,
    list_inbox_labels,
    update_label,
    delete_label,
    get_conversation_labels,
    attach_label_to_conversation,
    detach_label_from_conversation,
    update_tenant_config,
)
from schema import ConversationState, OperatorRole, Operator, Inbox, Label, ConversationRef
from background_jobs import run_grace_expiry_job

app = FastAPI(title="Inbox Allocation Service")


# =========================
# REQUEST MODELS
# =========================

class NewMessageRequest(BaseModel):
    tenant_id: str
    display_name: str
    external_conversation_id: str
    customer_phone_number: str
   


class ResolveRequest(BaseModel):
    operator_id: str
 


class OperatorAction(BaseModel):
    operator_id: str


class ReassignRequest(BaseModel):
    operator_id: str 
    target_operator_id: str


class MoveInboxRequest(BaseModel):
    operator_id: str  
    target_inbox_id: str


class CreateLabelRequest(BaseModel):
    name: str
    color: Optional[str] = None


class UpdateLabelRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


# =========================
# RESPONSE MODELS
# =========================

class ConversationResponse(BaseModel):
    id: str
    tenant_id: str
    inbox_id: str
    external_conversation_id: str
    customer_phone_number: str
    state: str
    assigned_operator_id: Optional[str]
    last_message_at: datetime
    message_count: int
    priority_score: float
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


class InboxResponse(BaseModel):
    id: str
    tenant_id: str
    phone_number: str
    display_name: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LabelResponse(BaseModel):
    id: str
    tenant_id: str
    inbox_id: str
    name: str
    color: Optional[str]
    created_by: str
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    conversations: List[ConversationResponse]
    total: int
    limit: int
    offset: int
    has_more: bool
    operator_status: Optional[str] = None


class OperatorStatusResponse(BaseModel):
    operator_id: str
    status: str
    last_status_change_at: datetime

    class Config:
        from_attributes = True


# =========================
# ORCHESTRATOR APIs
# =========================

@app.post("/orchestrator/message")
def on_message(req: NewMessageRequest):
    """
    Called when a new customer message arrives.
    Creates or updates conversation. Priority is NOT calculated here.
    Priority is calculated only during allocation.
    """
    inbox = get_or_create_inbox(
        tenant_id=req.tenant_id,
        phone_number=req.customer_phone_number,
        display_name=req.display_name,
    )

    conv = create_or_update_conversation(
        tenant_id=req.tenant_id,
        inbox_id=inbox.id,
        external_conversation_id=req.external_conversation_id,
        customer_phone_number=req.customer_phone_number,
    )
    return {"conversation_id": conv.id, "state": conv.state}


# =========================
# OPERATOR STATUS APIs
# =========================

@app.get("/operator/{operator_id}/status")
def get_status(operator_id: str):
    """Read operator status."""
    status = get_operator_status(operator_id)
    if not status:
        return "Operator Invalid"
    return OperatorStatusResponse(
        operator_id=status.operator_id,
        status=status.status.value,
        last_status_change_at=status.last_status_change_at,
    )


@app.post("/operator/{operator_id}/updatestatus")
def update_status(operator_id: str, new_status: str):
    """Update operator status (AVAILABLE or OFFLINE)."""
    from schema import OperatorAvailability
    
    if new_status.upper() == "OFFLINE":
        operator_goes_offline(operator_id)
    elif new_status.upper() == "AVAILABLE":
        operator_goes_online(operator_id)
    else:
        raise HTTPException(400, "Status must be AVAILABLE or OFFLINE")
    
    status = get_operator_status(operator_id)
    if not status:
        raise HTTPException(404, "Operator not found")
    return OperatorStatusResponse(
        operator_id=status.operator_id,
        status=status.status.value,
        last_status_change_at=status.last_status_change_at,
    )





# =========================
# INBOX APIs
# =========================

@app.get("/operator/{operator_id}/inboxes")
def get_inboxes(operator_id: str):
    """List inboxes subscribed by operator."""
    inboxes = get_operator_inboxes(operator_id)
    return {
        "inboxes": [
            InboxResponse(
                id=inbox.id,
                tenant_id=inbox.tenant_id,
                phone_number=inbox.phone_number,
                display_name=inbox.display_name,
                created_at=inbox.created_at,
                updated_at=inbox.updated_at,
            )
            for inbox in inboxes
        ]
    }


# =========================
# CONVERSATION LIST APIs
# =========================

@app.get("/conversations")
def list_conversations_endpoint(
    operator_id: str = Query(..., description="Operator ID"),
):
    """
    List QUEUED conversations for operator.
    Works like auto_allocate but returns list instead of allocating:
    1. Get tenant_id from operator_id
    2. Check operator is AVAILABLE
    3. Get latest 100 QUEUED conversations for tenant
    4. Calculate and update priority scores
    5. Sort by priority_score desc, then last_message_at asc
    6. Return list
    """
    # Trim whitespace
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    conversations, operator_status = list_queued_conversations_for_operator(operator_id)
    
    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                id=c.id,
                tenant_id=c.tenant_id,
                inbox_id=c.inbox_id,
                external_conversation_id=c.external_conversation_id,
                customer_phone_number=c.customer_phone_number,
                state=c.state.value,
                assigned_operator_id=c.assigned_operator_id,
                last_message_at=c.last_message_at,
                message_count=c.message_count,
                priority_score=c.priority_score,
                created_at=c.created_at,
                updated_at=c.updated_at,
                resolved_at=c.resolved_at,
            )
            for c in conversations
        ],
        total=len(conversations),
        limit=100,
        offset=0,
        has_more=False,
        operator_status=operator_status
    )


# =========================
# ALLOCATION APIs
# =========================

@app.post("/operator/{operator_id}/allocate")
def auto_allocate(operator_id: str):
    """Auto allocate next conversation sorted by priority."""
    # Trim whitespace
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    print(f"[API] Auto allocate request for operator_id: {operator_id}")
    conv = allocate_next_conversation(operator_id)
    if not conv:
        print(f"[API] No conversation allocated for operator_id: {operator_id}")
        return {"message": "No conversations available"}
    
    print(f"[API] Successfully allocated conversation {conv.id} to operator {operator_id}")
    return ConversationResponse(
        id=conv.id,
        tenant_id=conv.tenant_id,
        inbox_id=conv.inbox_id,
        external_conversation_id=conv.external_conversation_id,
        customer_phone_number=conv.customer_phone_number,
        state=conv.state.value,
        assigned_operator_id=conv.assigned_operator_id,
        last_message_at=conv.last_message_at,
        message_count=conv.message_count,
        priority_score=conv.priority_score,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        resolved_at=conv.resolved_at,
    )


@app.post("/operator/conversation/{conversation_id}/claim")
def manual_claim(conversation_id: str, action: OperatorAction):
    """Manual claim of a queued conversation."""
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    operator_id = action.operator_id.strip() if action.operator_id else None
    
    if not conversation_id or not operator_id:
        raise HTTPException(400, "conversation_id and operator_id are required")
    
    print(f"[API] Manual claim request - conversation_id: {conversation_id}, operator_id: {operator_id}")
    conv = claim_conversation(conversation_id, operator_id)
    if not conv:
        print(f"[API] Claim failed - conversation_id: {conversation_id}, operator_id: {operator_id}")
        raise HTTPException(409, "Conversation already taken or not available")
    
    print(f"[API] Successfully claimed conversation {conv.id} by operator {operator_id}")
    return ConversationResponse(
        id=conv.id,
        tenant_id=conv.tenant_id,
        inbox_id=conv.inbox_id,
        external_conversation_id=conv.external_conversation_id,
        customer_phone_number=conv.customer_phone_number,
        state=conv.state.value,
        assigned_operator_id=conv.assigned_operator_id,
        last_message_at=conv.last_message_at,
        message_count=conv.message_count,
        priority_score=conv.priority_score,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        resolved_at=conv.resolved_at,
    )


@app.post("/operator/conversation/{conversation_id}/resolve")
def resolve(conversation_id: str, req: ResolveRequest):
    """
    Resolve conversation.
    Allowed for:
    - Owner (assigned_operator_id matches operator_id)
    - Manager/Admin of same tenant
    """
    try:
        conv = resolve_conversation(
            conversation_id,
            req.operator_id
        )
        if not conv:
            raise HTTPException(404, "Conversation not found")
        return ConversationResponse(
            id=conv.id,
            tenant_id=conv.tenant_id,
            inbox_id=conv.inbox_id,
            external_conversation_id=conv.external_conversation_id,
            customer_phone_number=conv.customer_phone_number,
            state=conv.state.value,
            assigned_operator_id=conv.assigned_operator_id,
            last_message_at=conv.last_message_at,
            message_count=conv.message_count,
            priority_score=conv.priority_score,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            resolved_at=conv.resolved_at,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))


# =========================
# MANAGER / ADMIN APIs
# =========================

@app.post("/manager/conversation/{conversation_id}/deallocate")
def manager_deallocate(conversation_id: str, operator_id: str = Query(...)):
    """Deallocate conversation. Only MANAGER or ADMIN."""
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    operator_id = operator_id.strip() if operator_id else None
    
    print(f"[API] Deallocate request - conversation_id: {conversation_id}, operator_id: {operator_id}")
    
    if not conversation_id or not operator_id:
        print(f"[API] ERROR: Missing conversation_id or operator_id")
        raise HTTPException(400, "conversation_id and operator_id are required")
    
    if not is_manager_or_admin(operator_id):
        print(f"[API] ERROR: Operator {operator_id} is not MANAGER or ADMIN")
        raise HTTPException(403, "Only MANAGER or ADMIN can deallocate")
    
    print(f"[API] Permission check passed, calling deallocate_conversation")
    conv = deallocate_conversation(conversation_id)
    if not conv:
        print(f"[API] ERROR: Deallocate failed - conversation not found or not ALLOCATED")
        raise HTTPException(404, "Conversation not found or not in ALLOCATED state")
    
    print(f"[API] SUCCESS: Deallocated conversation {conv.id}")
    return ConversationResponse(
        id=conv.id,
        tenant_id=conv.tenant_id,
        inbox_id=conv.inbox_id,
        external_conversation_id=conv.external_conversation_id,
        customer_phone_number=conv.customer_phone_number,
        state=conv.state.value,
        assigned_operator_id=conv.assigned_operator_id,
        last_message_at=conv.last_message_at,
        message_count=conv.message_count,
        priority_score=conv.priority_score,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        resolved_at=conv.resolved_at,
    )


@app.post("/manager/conversation/{conversation_id}/reassign")
def reassign(conversation_id: str, req: ReassignRequest):
    """Reassign conversation to another operator. Only MANAGER or ADMIN."""
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    req.operator_id = req.operator_id.strip() if req.operator_id else None
    req.target_operator_id = req.target_operator_id.strip() if req.target_operator_id else None
    
    print(f"[API] Reassign request - conversation_id: {conversation_id}, operator_id: {req.operator_id}, target_operator_id: {req.target_operator_id}")
    
    if not conversation_id or not req.operator_id or not req.target_operator_id:
        print(f"[API] ERROR: Missing required parameters")
        raise HTTPException(400, "conversation_id, operator_id, and target_operator_id are required")
    
    if not is_manager_or_admin(req.operator_id):
        print(f"[API] ERROR: Operator {req.operator_id} is not MANAGER or ADMIN")
        raise HTTPException(403, "Only MANAGER or ADMIN can reassign")
    
    print(f"[API] Permission check passed, calling reassign_conversation")
    try:
        conv = reassign_conversation(conversation_id, req.target_operator_id, req.operator_id)
        if not conv:
            print(f"[API] ERROR: Reassign failed - conversation not found or not ALLOCATED")
            raise HTTPException(404, "Conversation not found or not in ALLOCATED state")
        
        print(f"[API] SUCCESS: Reassigned conversation {conv.id}")
        return ConversationResponse(
            id=conv.id,
            tenant_id=conv.tenant_id,
            inbox_id=conv.inbox_id,
            external_conversation_id=conv.external_conversation_id,
            customer_phone_number=conv.customer_phone_number,
            state=conv.state.value,
            assigned_operator_id=conv.assigned_operator_id,
            last_message_at=conv.last_message_at,
            message_count=conv.message_count,
            priority_score=conv.priority_score,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            resolved_at=conv.resolved_at,
        )
    except PermissionError as e:
        print(f"[API] ERROR: Permission denied - {str(e)}")
        raise HTTPException(403, str(e))


@app.post("/manager/conversation/{conversation_id}/move_inbox")
def move_inbox(conversation_id: str, req: MoveInboxRequest):
    """Move conversation to another inbox in same tenant. Only MANAGER or ADMIN."""
    # Trim whitespace
    conversation_id = conversation_id.strip() if conversation_id else None
    req.operator_id = req.operator_id.strip() if req.operator_id else None
    req.target_inbox_id = req.target_inbox_id.strip() if req.target_inbox_id else None
    
    print(f"[API] Move inbox request - conversation_id: {conversation_id}, operator_id: {req.operator_id}, target_inbox_id: {req.target_inbox_id}")
    
    if not conversation_id or not req.operator_id or not req.target_inbox_id:
        print(f"[API] ERROR: Missing required parameters")
        raise HTTPException(400, "conversation_id, operator_id, and target_inbox_id are required")
    
    if not is_manager_or_admin(req.operator_id):
        print(f"[API] ERROR: Operator {req.operator_id} is not MANAGER or ADMIN")
        raise HTTPException(403, "Only MANAGER or ADMIN can move conversations")
    
    print(f"[API] Permission check passed, calling move_conversation_inbox")
    try:
        conv = move_conversation_inbox(conversation_id, req.target_inbox_id, req.operator_id)
        if not conv:
            print(f"[API] ERROR: Move inbox failed - conversation not found")
            raise HTTPException(404, "Conversation not found")
        
        print(f"[API] SUCCESS: Moved conversation {conv.id} to inbox {req.target_inbox_id}")
        return ConversationResponse(
            id=conv.id,
            tenant_id=conv.tenant_id,
            inbox_id=conv.inbox_id,
            external_conversation_id=conv.external_conversation_id,
            customer_phone_number=conv.customer_phone_number,
            state=conv.state.value,
            assigned_operator_id=conv.assigned_operator_id,
            last_message_at=conv.last_message_at,
            message_count=conv.message_count,
            priority_score=conv.priority_score,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            resolved_at=conv.resolved_at,
        )
    except (PermissionError, ValueError) as e:
        print(f"[API] ERROR: {str(e)}")
        raise HTTPException(400, str(e))


# =========================
# LABEL APIs
# =========================

@app.post("/inbox/{inbox_id}/labels")
def create_label_endpoint(inbox_id: str, req: CreateLabelRequest, operator_id: str = Query(...)):
    """Create label in inbox. Only ADMIN can create labels."""
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    if not is_admin(operator_id):
        raise HTTPException(403, "Only ADMIN can create labels")
    
    # Get tenant_id from inbox
    from database_operations import SessionLocal
    with SessionLocal() as db:
        inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
        if not inbox:
            raise HTTPException(404, "Inbox not found")
        tenant_id = inbox.tenant_id
    
    label = create_label(tenant_id, inbox_id, req.name, operator_id, req.color)
    return LabelResponse(
        id=label.id,
        tenant_id=label.tenant_id,
        inbox_id=label.inbox_id,
        name=label.name,
        color=label.color,
        created_by=label.created_by,
        created_at=label.created_at,
    )


@app.get("/inbox/{inbox_id}/labels")
def list_labels(inbox_id: str, operator_id: str = Query(...)):
    """List all labels for an inbox. Only ADMIN can list labels."""
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    if not is_admin(operator_id):
        raise HTTPException(403, "Only ADMIN can list labels")
    
    labels = list_inbox_labels(inbox_id)
    return {
        "labels": [
            LabelResponse(
                id=label.id,
                tenant_id=label.tenant_id,
                inbox_id=label.inbox_id,
                name=label.name,
                color=label.color,
                created_by=label.created_by,
                created_at=label.created_at,
            )
            for label in labels
        ]
    }


@app.put("/labels/{label_id}")
def update_label_endpoint(label_id: str, req: UpdateLabelRequest, operator_id: str = Query(...)):
    """Update label. Only ADMIN can update labels."""
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    if not is_admin(operator_id):
        raise HTTPException(403, "Only ADMIN can update labels")
    
    label = update_label(label_id, req.name, req.color)
    if not label:
        raise HTTPException(404, "Label not found")
    return LabelResponse(
        id=label.id,
        tenant_id=label.tenant_id,
        inbox_id=label.inbox_id,
        name=label.name,
        color=label.color,
        created_by=label.created_by,
        created_at=label.created_at,
    )


@app.delete("/labels/{label_id}")
def delete_label_endpoint(label_id: str, operator_id: str = Query(...)):
    """Delete label. Only ADMIN can delete labels."""
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    if not is_admin(operator_id):
        raise HTTPException(403, "Only ADMIN can delete labels")
    
    success = delete_label(label_id)
    if not success:
        raise HTTPException(404, "Label not found")
    return {"message": "Label deleted successfully"}


@app.get("/conversation/{conversation_id}/labels")
def get_conversation_labels_endpoint(conversation_id: str, operator_id: str = Query(...)):
    """Get all labels attached to conversation. Only ADMIN can get labels."""
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    if not is_admin(operator_id):
        raise HTTPException(403, "Only ADMIN can get conversation labels")
    
    labels = get_conversation_labels(conversation_id)
    return {
        "labels": [
            LabelResponse(
                id=label.id,
                tenant_id=label.tenant_id,
                inbox_id=label.inbox_id,
                name=label.name,
                color=label.color,
                created_by=label.created_by,
                created_at=label.created_at,
            )
            for label in labels
        ]
    }


@app.post("/conversation/{conversation_id}/labels/{label_id}")
def attach_label(conversation_id: str, label_id: str, operator_id: str = Query(...)):
    """Attach label to conversation. Only ADMIN can attach labels."""
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    if not is_admin(operator_id):
        raise HTTPException(403, "Only ADMIN can attach labels")
    
    mapping = attach_label_to_conversation(conversation_id, label_id)
    return {"message": "Label attached successfully", "mapping_id": mapping.id}


@app.delete("/conversation/{conversation_id}/labels/{label_id}")
def detach_label(conversation_id: str, label_id: str, operator_id: str = Query(...)):
    """Detach label from conversation. Only ADMIN can detach labels."""
    operator_id = operator_id.strip() if operator_id else None
    
    if not operator_id:
        raise HTTPException(400, "operator_id is required")
    
    if not is_admin(operator_id):
        raise HTTPException(403, "Only ADMIN can detach labels")
    
    success = detach_label_from_conversation(conversation_id, label_id)
    if not success:
        raise HTTPException(404, "Label not attached to conversation")
    return {"message": "Label detached successfully"}


# =========================
# SEARCH APIs
# =========================

@app.get("/search")
def search_by_phone(
    tenant_id: str = Query(..., description="Tenant ID"),
    phone_number: str = Query(..., description="Phone number (exact match)"),
):
    """Search conversations by phone number (exact match)."""
    conversations = search_conversations_by_phone(tenant_id, phone_number)
    return {
        "conversations": [
            ConversationResponse(
                id=c.id,
                tenant_id=c.tenant_id,
                inbox_id=c.inbox_id,
                external_conversation_id=c.external_conversation_id,
                customer_phone_number=c.customer_phone_number,
                state=c.state.value,
                assigned_operator_id=c.assigned_operator_id,
                last_message_at=c.last_message_at,
                message_count=c.message_count,
                priority_score=c.priority_score,
                created_at=c.created_at,
                updated_at=c.updated_at,
                resolved_at=c.resolved_at,
            )
            for c in conversations
        ]
    }




# =========================
# TENANT CONFIG APIs
# =========================

class UpdateTenantConfigRequest(BaseModel):
    alpha: Optional[float] = None
    beta: Optional[float] = None


@app.put("/admin/tenant/{tenant_id}/config")
def update_tenant_config_endpoint(tenant_id: str, req: UpdateTenantConfigRequest, operator_id: str = Query(...)):
    """Update tenant alpha/beta values. Only ADMIN can update tenant config."""
    operator_id = operator_id.strip() if operator_id else None
    tenant_id = tenant_id.strip() if tenant_id else None
    print(f"[API] Update tenant config request - tenant_id: {tenant_id}, operator_id: {operator_id}, req: {req}")
    
    if not operator_id or not tenant_id:
        raise HTTPException(400, "operator_id and tenant_id are required")
    
    if not is_admin(operator_id):
        raise HTTPException(403, "Only ADMIN can update tenant config")
    
    if req.alpha is None and req.beta is None:
        raise HTTPException(400, "At least one of alpha or beta must be provided")
    
    tenant = update_tenant_config(tenant_id, req.alpha, req.beta)
    print(f"[API] Updated tenant config - tenant: {tenant}")
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    
    return {
        "tenant_id": tenant.tenant_id,
        "alpha": tenant.alpha,
        "beta": tenant.beta,
        "updated_at": tenant.updated_at
    }


# =========================
# BACKGROUND JOBS
# =========================

@app.post("/admin/grace-expiry/run")
def run_grace_expiry(background_tasks: BackgroundTasks):
    """Manually trigger grace expiry job (for testing/admin)."""
    background_tasks.add_task(run_grace_expiry_job)
    return {"message": "Grace expiry job scheduled"}


# =========================
# STARTUP EVENT
# =========================

@app.on_event("startup")
async def startup_event():
    """Start background scheduler for grace expiry monitoring."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            run_grace_expiry_job,
            trigger=IntervalTrigger(minutes=1),
            id="grace_expiry_job",
            replace_existing=True,
        )
        scheduler.start()
        print("✅ Background scheduler started for grace expiry monitoring")
    except ImportError:
        print("⚠️  APScheduler not installed. Install with: pip install apscheduler")
        print("   Grace expiry monitoring will not run automatically.")
