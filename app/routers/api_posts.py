from fastapi import APIRouter, HTTPException, status

from app.schemas import PostCreate, PostResponse

router = APIRouter(prefix="/api/posts", tags=["posts"])
