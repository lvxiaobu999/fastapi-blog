from fastapi import APIRouter, HTTPException, status

from app.schemas import PostCreate, PostResponse
from app.services.posts import create_post, get_post

router = APIRouter(prefix="/api/posts", tags=["posts"])


@router.get("/{post_id}", response_model=PostResponse)
def read_post(post_id: int) -> PostResponse:
    post = get_post(post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def add_post(post: PostCreate) -> PostResponse:
    return create_post(post)
