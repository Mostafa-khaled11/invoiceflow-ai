"""Version 1 API router."""

from fastapi import APIRouter

from app.api.v1.endpoints import health, invoices, review_cases

router = APIRouter()
router.include_router(health.router, tags=["system"])
router.include_router(invoices.router)
router.include_router(review_cases.router)
