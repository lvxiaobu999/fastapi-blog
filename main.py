from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.schemas import PostCreate, PostResponse

app = FastAPI()

app.mount("/static", StaticFiles(directory="apps/static"), name="static")

templates = Jinja2Templates(directory="apps/templates")


@app.exception_handler(404)
def not_found(request: Request, exc: StarletteHTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "The requested page was not found."
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": "Page not found", "status_code": 404, "message": message},
        status_code=status.HTTP_404_NOT_FOUND,
    )

posts: list[PostResponse] = [
    PostResponse(
        id=1,
        author="Corey Schafer",
        title="FastAPI is Awesome",
        date_posted="April 20, 2025",
        content="This framework is really easy to use and super fast.",
    ),
    PostResponse(
        id=2,
        author="Jane Doe",
        title="Python is Great for Web Development",
        content="Python is a great language for web development, and FastAPI makes it even",
        date_posted="April 21,2025",
    ),
]


@app.get("/", include_in_schema=False, name="home")
@app.get("/posts", include_in_schema=False, name="posts")
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"posts": posts, "title": "home"})


@app.get("/posts/{post_id}", include_in_schema=False, name="post")
def post(request: Request, post_id: int):
    for post in posts:
        if post.id == post_id:
            return templates.TemplateResponse(
                request,
                "post.html",
                {"post": post, "title": "post detail"},  # noqa: F821
            )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")


@app.get("/api/posts/{post_id}", response_model=PostResponse)
def get_post(post_id: int) -> PostResponse:
    for post in posts:
        if post.id == post_id:
            return post
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")


@app.post("/api/posts", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def create_post(post: PostCreate) -> PostResponse:
    new_post = PostResponse(
        id=max((existing_post.id for existing_post in posts), default=0) + 1,
        date_posted=datetime.now().strftime("%B %d, %Y"),
        **post.model_dump(),
    )
    posts.append(new_post)
    return new_post
