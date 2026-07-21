from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.templating import templates

router = APIRouter(include_in_schema=False)
