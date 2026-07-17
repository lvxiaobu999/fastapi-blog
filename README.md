# FastAPI Blog

## Run locally

```powershell
uv run fastapi dev app/main.py
```

## Project structure

```text
app/
├── main.py          # Application assembly and exception handlers
├── routers/         # Page and API routes
├── schemas/         # Pydantic request and response models
├── services/        # In-memory post operations
├── static/          # CSS and JavaScript
└── templates/       # Jinja templates
```
