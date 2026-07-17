from datetime import datetime

from app.schemas import PostCreate, PostResponse

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


def get_post(post_id: int) -> PostResponse | None:
    return next((post for post in posts if post.id == post_id), None)


def create_post(post: PostCreate) -> PostResponse:
    new_post = PostResponse(
        id=max((existing_post.id for existing_post in posts), default=0) + 1,
        date_posted=datetime.now().strftime("%B %d, %Y"),
        **post.model_dump(),
    )
    posts.append(new_post)
    return new_post
