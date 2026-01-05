"""
Background jobs for the Inbox Allocation Service.
"""
import logging
from allocation_engine import process_grace_expiry

logger = logging.getLogger(__name__)


def run_grace_expiry_job():
    """
    Background job to process expired grace periods.
    Should be called periodically (e.g., every 1 minute).
    """
    try:
        process_grace_expiry()
        logger.info("Grace expiry job completed successfully")
    except Exception as e:
        logger.error(f"Error in grace expiry job: {e}", exc_info=True)

