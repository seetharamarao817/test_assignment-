import os
from datetime import datetime
from typing import List, Optional, Tuple
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from schema import ConversationRef, Tenant

# =========================
# DATABASE SETUP
# =========================

DATABASE_PATH = os.getenv("DATABASE_PATH", "./inbox_allocation.db")
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

# =========================
# DEFAULT CONFIG
# =========================

DEFAULT_ALPHA = 1.0  
DEFAULT_BETA = 1.0   
DEFAULT_MAX_MESSAGE_COUNT = 100  
DEFAULT_MAX_DELAY_MINUTES = 1440  


# =========================
# GET TENANT CONFIG
# =========================

def get_tenant_config(tenant_id: str) -> Tuple[float, float]:
    """Get alpha and beta weights for tenant. Returns defaults if not found."""
    with SessionLocal() as db:
        tenant = db.execute(
            select(Tenant).where(Tenant.tenant_id == tenant_id)
        ).scalar_one_or_none()
        
        if tenant:
            return tenant.alpha, tenant.beta
        return DEFAULT_ALPHA, DEFAULT_BETA




# =========================
# PRIORITY CALCULATION (NORMALIZED)
# =========================

def normalize_value(value: float, min_val: float, max_val: float) -> float:
    """Normalize value to 0-1 range. Returns 0 if min==max."""
    if max_val == min_val:
        return 0.0
    return (value - min_val) / (max_val - min_val)


def calculate_normalized_priority_with_candidates(
    conv: ConversationRef,
    candidate_set: List[ConversationRef],
    tenant_id: str
) -> float:
    """
    Calculate normalized priority using candidate set for normalization.
    Used during allocation.
    """
    if not candidate_set:
        return 0.0
    
    alpha, beta = get_tenant_config(tenant_id)

    
    if len(candidate_set) == 1:
        return alpha + beta
    
    now = datetime.utcnow()
    
    delays = []
    message_counts = []
    
    for c in candidate_set:
        delay_minutes = (
            (now - c.last_message_at).total_seconds() / 60
            if c.last_message_at
            else 0
        )
        delays.append(delay_minutes)
        message_counts.append(c.message_count)
    
    min_delay = min(delays) if delays else 0
    max_delay = max(delays) if delays else DEFAULT_MAX_DELAY_MINUTES
    min_count = min(message_counts) if message_counts else 0
    max_count = max(message_counts) if message_counts else DEFAULT_MAX_MESSAGE_COUNT
    
    current_delay = (
        (now - conv.last_message_at).total_seconds() / 60
        if conv.last_message_at
        else 0
    )
    current_count = conv.message_count
    
    normalized_delay = normalize_value(current_delay, min_delay, max_delay)
    normalized_count = normalize_value(current_count, min_count, max_count)
    
    priority = alpha * normalized_count + beta * normalized_delay
    
    return priority


