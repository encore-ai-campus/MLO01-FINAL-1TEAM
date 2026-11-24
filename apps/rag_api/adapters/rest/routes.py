"""REST 라우트. 판정·데이터 접근은 기능 모듈이 함(adapters.md)."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")
