# Inbox Allocation Service - Complete Documentation

A FastAPI-based backend service for managing inbox conversations, operator allocation, priority-based conversation routing, and label management.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Project Structure](#project-structure)
4. [Database Setup](#database-setup)
5. [Running the API Server](#running-the-api-server)
6. [API Endpoints](#api-endpoints)
7. [Testing Examples](#testing-examples)
8. [File Details](#file-details)
9. [Configuration](#configuration)

---

## Overview

This service provides:
- **Conversation Management**: Create, update, and track customer conversations
- **Auto Allocation**: Priority-based automatic conversation assignment to operators
- **Manual Claim**: Operators can manually claim conversations
- **Grace Period**: Buffer time when operators go offline before conversations are re-queued
- **Label Management**: Admin-only label creation and attachment to conversations
- **Tenant Configuration**: Configurable priority weights (alpha/beta) per tenant
- **Role-Based Access**: OPERATOR, MANAGER, and ADMIN roles with different permissions

---

## Prerequisites

### Required Software

- **Python 3.8+**
- **pip** (Python package manager)
- **SQLite** (included with Python)

### Required Python Packages

Install dependencies:

```bash
pip install fastapi uvicorn sqlalchemy apscheduler pydantic
```

Or use the requirements file (if available):

```bash
pip install -r requirements.txt
```

### Package Versions

- `fastapi>=0.104.0`
- `uvicorn>=0.24.0`
- `sqlalchemy>=2.0.0`
- `apscheduler>=3.10.0`
- `pydantic>=2.0.0`

---

## Project Structure

```
test_assignment/
├── api.py                      # FastAPI application and endpoints
├── schema.py                   # SQLAlchemy ORM models and database schema
├── database_operations.py     # Database CRUD operations
├── allocation_engine.py       # Core allocation, claim, resolve logic
├── priority_engine.py         # Priority calculation engine
├── background_jobs.py         # Background tasks (grace expiry)
└── README.md                  # This file
```

---

## Database Setup

### Step 1: Create Database Schema

The database schema is automatically created when you run the schema file:

```bash
python schema.py
```

**Output:**
```
✅ SQLite database and tables created successfully
```

This creates:
- SQLite database file: `inbox_allocation.db`
- All required tables (operators, conversations, labels, etc.)

### Step 2: Verify Database Creation

Check that `inbox_allocation.db` exists in your project directory:

```bash
ls -lh inbox_allocation.db
```

### Database Schema Overview

**Core Tables:**
- `operators` - Operator accounts with roles (OPERATOR, MANAGER, ADMIN)
- `operator_status` - Current availability status (AVAILABLE, OFFLINE)
- `inboxes` - Customer inboxes (phone numbers)
- `conversation_refs` - Conversation records with priority scores
- `labels` - Labels for categorizing conversations
- `conversation_labels` - Many-to-many relationship between conversations and labels
- `operator_inbox_subscriptions` - Operator inbox access
- `grace_period_assignments` - Grace period tracking when operators go offline
- `tenants` - Tenant configuration (alpha/beta weights)

**ID Format:**
- Conversations: `conv-{5chars}` (e.g., `conv-66acc`)
- Operators: `op-{5chars}` (e.g., `op-13a8f`)
- Inboxes: `inbox-{5chars}` (e.g., `inbox-c9cd3`)

---

## Running the API Server

### Start the Server

```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

**Options:**
- `--reload`: Auto-reload on code changes (development)
- `--host 0.0.0.0`: Listen on all interfaces
- `--port 8000`: Port number

### Verify Server is Running

Open browser or use curl:

```bash
curl http://localhost:8000/docs
```

You should see the FastAPI interactive documentation (Swagger UI).

### Server Endpoints

- **API Base URL**: `http://localhost:8000`
- **Interactive Docs**: `http://localhost:8000/docs`
- **Alternative Docs**: `http://localhost:8000/redoc`

---

## API Endpoints

### 1. Orchestrator APIs

#### Create/Update Conversation
```http
POST /orchestrator/message
Content-Type: application/json

{
  "tenant_id": "tenant-test-001",
  "display_name": "Test Customer",
  "external_conversation_id": "ext-123",
  "customer_phone_number": "+1234567890"
}
```

**Response:**
```json
{
  "conversation_id": "conv-66acc",
  "state": "QUEUED"
}
```

---

### 2. Operator Status APIs

#### Get Operator Status
```http
GET /operator/{operator_id}/status
```

**Example:**
```bash
curl http://localhost:8000/operator/op-13a8f/status
```

#### Update Operator Status
```http
POST /operator/{operator_id}/updatestatus?new_status=AVAILABLE
```

**Example:**
```bash
curl -X POST "http://localhost:8000/operator/op-13a8f/updatestatus?new_status=AVAILABLE"
```

---

### 3. Conversation List APIs

#### List QUEUED Conversations
```http
GET /conversations?operator_id={operator_id}
```

**Example:**
```bash
curl "http://localhost:8000/conversations?operator_id=op-41d15"
```

**Response:**
```json
{
  "conversations": [
    {
      "id": "conv-66acc",
      "tenant_id": "tenant-test-001",
      "inbox_id": "inbox-c9cd3",
      "state": "QUEUED",
      "priority_score": 2.5,
      ...
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0,
  "has_more": false,
  "operator_status": "AVAILABLE"
}
```

---

### 4. Allocation APIs

#### Auto Allocate Next Conversation
```http
POST /operator/{operator_id}/allocate
```

**Example:**
```bash
curl -X POST http://localhost:8000/operator/op-41d15/allocate
```

#### Manual Claim Conversation
```http
POST /operator/conversation/{conversation_id}/claim
Content-Type: application/json

{
  "operator_id": "op-41d15"
}
```

#### Resolve Conversation
```http
POST /operator/conversation/{conversation_id}/resolve
Content-Type: application/json

{
  "operator_id": "op-41d15"
}
```

---

### 5. Manager/Admin APIs

#### Deallocate Conversation
```http
POST /manager/conversation/{conversation_id}/deallocate?operator_id={manager_id}
```

**Example:**
```bash
curl -X POST "http://localhost:8000/manager/conversation/conv-66acc/deallocate?operator_id=op-63846"
```

#### Reassign Conversation
```http
POST /manager/conversation/{conversation_id}/reassign
Content-Type: application/json

{
  "operator_id": "op-63846",
  "target_operator_id": "op-0a706"
}
```

**Note:** Works for RESOLVED or QUEUED conversations. ALLOCATED conversations must be deallocated first.

#### Move Conversation to Another Inbox
```http
POST /manager/conversation/{conversation_id}/move_inbox
Content-Type: application/json

{
  "operator_id": "op-63846",
  "target_inbox_id": "inbox-7bca3"
}
```

**Example:**
```bash
curl -X POST http://localhost:8000/manager/conversation/conv-66acc/move_inbox \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "op-63846",
    "target_inbox_id": "inbox-7bca3"
  }'
```

---

### 6. Label APIs (Admin Only)

#### Create Label
```http
POST /inbox/{inbox_id}/labels?operator_id={admin_id}
Content-Type: application/json

{
  "name": "VIP",
  "color": "#FF0000"
}
```

**Note:** Automatically attaches label to all existing conversations in that inbox.

#### List Labels for Inbox
```http
GET /inbox/{inbox_id}/labels?operator_id={admin_id}
```

#### Update Label
```http
PUT /labels/{label_id}?operator_id={admin_id}
Content-Type: application/json

{
  "name": "VIP Customer",
  "color": "#FF0000"
}
```

#### Delete Label
```http
DELETE /labels/{label_id}?operator_id={admin_id}
```

**Note:** Automatically removes label from all conversations.

#### Get Labels for Conversation
```http
GET /conversation/{conversation_id}/labels?operator_id={admin_id}
```

#### Attach Label to Conversation
```http
POST /conversation/{conversation_id}/labels/{label_id}?operator_id={admin_id}
```

#### Detach Label from Conversation
```http
DELETE /conversation/{conversation_id}/labels/{label_id}?operator_id={admin_id}
```

---

### 7. Search APIs

#### Search Conversations by Phone
```http
GET /search?tenant_id={tenant_id}&phone_number={phone_number}
```

**Example:**
```bash
curl "http://localhost:8000/search?tenant_id=tenant-test-001&phone_number=%2B1234567890"
```

**Response:**
```json
{
  "conversations": [
    {
      "id": "conv-66acc",
      "customer_phone_number": "+1234567890",
      ...
    }
  ]
}
```

---

### 8. Tenant Config APIs (Admin Only)

#### Update Tenant Alpha/Beta Weights
```http
PUT /admin/tenant/{tenant_id}/config?operator_id={admin_id}
Content-Type: application/json

{
  "alpha": 1.5,
  "beta": 2.0
}
```

**Example:**
```bash
curl -X PUT "http://localhost:8000/admin/tenant/tenant-test-001/config?operator_id=op-13a8f" \
  -H "Content-Type: application/json" \
  -d '{"alpha": 1.5, "beta": 2.0}'
```

---

## Testing Examples

### Manual Testing Examples

#### 1. Create a Conversation

```bash
curl -X POST http://localhost:8000/orchestrator/message \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-test-001",
    "display_name": "Test Customer 1",
    "external_conversation_id": "ext-001",
    "customer_phone_number": "+1234567890"
  }'
```

#### 2. Set Operator to Available

```bash
curl -X POST "http://localhost:8000/operator/op-41d15/updatestatus?new_status=AVAILABLE"
```

#### 3. List Conversations

```bash
curl "http://localhost:8000/conversations?operator_id=op-41d15"
```

#### 4. Auto Allocate

```bash
curl -X POST http://localhost:8000/operator/op-41d15/allocate
```

#### 5. Create Label (Admin)

```bash
curl -X POST "http://localhost:8000/inbox/inbox-c9cd3/labels?operator_id=op-13a8f" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "VIP",
    "color": "#FF0000"
  }'
```

#### 6. Search by Phone

```bash
curl "http://localhost:8000/search?tenant_id=tenant-test-001&phone_number=%2B1234567890"
```

#### 7. Move Conversation

```bash
curl -X POST http://localhost:8000/manager/conversation/conv-66acc/move_inbox \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "op-63846",
    "target_inbox_id": "inbox-7bca3"
  }'
```

---

## File Details

### `schema.py`

**Purpose:** Database schema definition using SQLAlchemy ORM.

**Key Components:**
- **ID Generation Functions**: `generate_conversation_id()`, `generate_operator_id()`, `generate_inbox_id()`
- **Enums**: `OperatorRole`, `ConversationState`, `OperatorAvailability`, `GraceReason`
- **Tables**: All database models (Operator, ConversationRef, Label, etc.)

**Key Features:**
- Prefixed IDs (conv-, op-, inbox-)
- Foreign key relationships
- Indexes for performance
- Unique constraints

**Usage:**
```bash
python schema.py  # Creates database and tables
```

---

### `api.py`

**Purpose:** FastAPI application with all HTTP endpoints.

**Key Components:**
- **Request Models**: Pydantic models for request validation
- **Response Models**: Pydantic models for response serialization
- **Endpoints**: All API routes organized by category

**Endpoint Categories:**
1. Orchestrator APIs (`/orchestrator/*`)
2. Operator Status APIs (`/operator/{id}/status`)
3. Conversation List APIs (`/conversations`)
4. Allocation APIs (`/operator/{id}/allocate`, `/operator/conversation/{id}/claim`)
5. Manager/Admin APIs (`/manager/conversation/{id}/*`)
6. Label APIs (`/inbox/{id}/labels`, `/labels/*`, `/conversation/{id}/labels/*`)
7. Search APIs (`/search`)
8. Tenant Config APIs (`/admin/tenant/{id}/config`)

**Key Features:**
- Input validation and sanitization
- Role-based access control
- Error handling with HTTPException
- Background job scheduling (grace expiry)

**Startup Event:**
- Automatically starts APScheduler for grace expiry monitoring (runs every 1 minute)

---

### `database_operations.py`

**Purpose:** Database CRUD operations and business logic.

**Key Functions:**

**Conversation Operations:**
- `create_or_update_conversation()` - Create or update conversation (priority not calculated here)
- `search_conversations_by_phone()` - Search by phone number
- `reassign_conversation()` - Reassign to another operator (RESOLVED/QUEUED only)
- `move_conversation_inbox()` - Move conversation to another inbox

**Label Operations:**
- `create_label()` - Create label and auto-attach to all conversations in inbox
- `list_inbox_labels()` - List all labels for an inbox
- `update_label()` - Update label name/color
- `delete_label()` - Delete label and all conversation associations
- `get_conversation_labels()` - Get labels attached to conversation
- `attach_label_to_conversation()` - Manually attach label
- `detach_label_from_conversation()` - Remove label from conversation

**Tenant Operations:**
- `get_or_create_tenant_config()` - Get or create tenant config
- `get_tenant_config()` - Get tenant config
- `update_tenant_config()` - Update alpha/beta weights

**Key Features:**
- Proper session management (commit, refresh, expunge)
- Prevents DetachedInstanceError
- Transaction handling

---

### `allocation_engine.py`

**Purpose:** Core allocation, claim, resolve, and deallocation logic.

**Key Functions:**

**Allocation:**
- `allocate_next_conversation()` - Auto-allocate highest priority conversation
  - Gets tenant_id from operator
  - Checks operator is AVAILABLE
  - Gets top 100 QUEUED conversations
  - Calculates priority scores
  - Allocates top conversation
  - Auto-subscribes operator to inbox if needed

**Listing:**
- `list_queued_conversations_for_operator()` - List conversations (like allocate but returns list)
  - Works even if operator is OFFLINE
  - Returns operator_status in response

**Claim:**
- `claim_conversation()` - Manual claim by operator
  - Validates tenant match
  - Checks operator AVAILABLE
  - Checks conversation QUEUED
  - Auto-subscribes to inbox

**Resolve:**
- `resolve_conversation()` - Resolve conversation
  - Owner can resolve
  - Manager/Admin of same tenant can resolve

**Deallocate:**
- `deallocate_conversation()` - Manager/Admin deallocation
  - Changes ALLOCATED → QUEUED

**Grace Period:**
- `operator_goes_offline()` - Creates grace entries (1 minute default)
- `operator_goes_online()` - Removes grace entries
- `process_grace_expiry()` - Background job to expire grace periods

**Permission Checks:**
- `is_admin()` - Check if ADMIN
- `is_manager_or_admin()` - Check if MANAGER or ADMIN
- `get_operator_role()` - Get operator role
- `can_resolve_conversation()` - Check resolve permission

**Key Features:**
- Comprehensive logging for debugging
- Atomic operations
- Proper session management

---

### `priority_engine.py`

**Purpose:** Priority score calculation for conversations.

**Key Functions:**

**Priority Calculation:**
- `calculate_normalized_priority_with_candidates()` - Calculate priority using candidate set
  - Formula: `priority = alpha * normalized_count + beta * normalized_delay`
  - Normalizes message_count and delay to 0-1 range
  - Uses candidate set for normalization bounds
  - Special case: If only 1 candidate, returns `alpha + beta` (not 0)

**Normalization:**
- `normalize_value()` - Normalize value to 0-1 range
  - Returns 0 if min == max (prevents division by zero)

**Tenant Config:**
- `get_tenant_config()` - Get alpha/beta weights for tenant
  - Returns defaults (1.0, 1.0) if tenant not found

**Key Features:**
- Handles edge cases (single candidate, min==max)
- Tenant-specific weights
- Normalized scoring for fair comparison

**Priority Formula:**
```
priority_score = alpha * normalized_message_count + beta * normalized_delay

where:
- normalized_message_count = (count - min_count) / (max_count - min_count)
- normalized_delay = (delay - min_delay) / (max_delay - min_delay)
- delay = minutes since last_message_at
```

---

### `background_jobs.py`

**Purpose:** Background tasks for periodic operations.

**Key Functions:**
- `run_grace_expiry_job()` - Wrapper for grace expiry processing
  - Calls `process_grace_expiry()` from allocation_engine
  - Handles exceptions and logging

**Scheduling:**
- Configured in `api.py` startup event
- Runs every 1 minute via APScheduler
- Processes expired grace periods

**Key Features:**
- Error handling
- Logging
- Can be manually triggered via `/admin/grace-expiry/run`

---

## Configuration

### Environment Variables

**Database Path:**
```bash
export DATABASE_PATH="./inbox_allocation.db"
```

Default: `./inbox_allocation.db`

### Default Values

**Priority Weights:**
- `alpha` (message_count weight): `1.0`
- `beta` (delay weight): `1.0`

**Normalization Defaults:**
- `DEFAULT_MAX_MESSAGE_COUNT`: `100`
- `DEFAULT_MAX_DELAY_MINUTES`: `1440` (24 hours)

**Grace Period:**
- Default grace minutes: `1` minute

**Background Jobs:**
- Grace expiry check interval: `1` minute

---

## Common Issues and Solutions

### Issue: DetachedInstanceError

**Cause:** SQLAlchemy object accessed after session closed.

**Solution:** All functions now use `db.refresh()` and `db.expunge()` pattern.

### Issue: Priority Score is 0

**Cause:** Single conversation or all values same (min==max).

**Solution:** Fixed - single conversation now returns `alpha + beta` instead of 0.

### Issue: Grace Expiry Not Working

**Cause:** APScheduler not installed or not started.

**Solution:**
```bash
pip install apscheduler
```

Server startup will show: `✅ Background scheduler started for grace expiry monitoring`

### Issue: Permission Denied Errors

**Cause:** Wrong role for operation.

**Solutions:**
- Label operations require ADMIN
- Manager operations require MANAGER or ADMIN
- Check operator role: `GET /operator/{id}/status` (if implemented)

---

## Development Tips

### Enable SQL Query Logging

In `schema.py`, change:
```python
engine = create_engine(DATABASE_URL, echo=True)  # Shows all SQL queries
```

### Debug Logging

All functions have print statements with prefixes:
- `[ALLOCATE]` - Auto allocation
- `[CLAIM]` - Manual claim
- `[RESOLVE]` - Resolve conversation
- `[DEALLOCATE]` - Deallocate conversation
- `[LIST]` - List conversations
- `[REASSIGN]` - Reassign conversation
- `[MOVE_INBOX]` - Move conversation
- `[API]` - API endpoint logs

### Database Inspection

Use SQLite CLI:
```bash
sqlite3 inbox_allocation.db

# List tables
.tables

# View conversations
SELECT * FROM conversation_refs LIMIT 10;

# View operators
SELECT * FROM operators;

# Exit
.quit
```

---

## API Response Examples

### Success Response
```json
{
  "id": "conv-66acc",
  "tenant_id": "tenant-test-001",
  "state": "QUEUED",
  "priority_score": 2.5
}
```

### Error Response
```json
{
  "detail": "Only ADMIN can create labels"
}
```

HTTP Status Codes:
- `200` - Success
- `400` - Bad Request (validation error)
- `403` - Forbidden (permission denied)
- `404` - Not Found
- `409` - Conflict (e.g., conversation already taken)

---

## Testing Checklist

- [ ] Database schema created successfully
- [ ] API server starts without errors
- [ ] Create conversation works
- [ ] Auto allocate works
- [ ] Manual claim works
- [ ] Resolve works (owner and manager/admin)
- [ ] Deallocate works (manager/admin)
- [ ] Reassign works (RESOLVED/QUEUED only)
- [ ] Move inbox works (same tenant)
- [ ] Label create works (admin only, auto-attach)
- [ ] Label delete works (cascades to conversations)
- [ ] Search by phone works
- [ ] Grace expiry works (background job)
- [ ] Tenant config update works (admin only)

---

## Support

For issues or questions:
1. Check logs in console output
2. Verify database schema is correct
3. Check operator roles and permissions
4. Verify tenant_id matches across operations



**Last Updated:** 2026-01-05

