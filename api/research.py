import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app

async def handler(scope, receive, send):
    await app(scope, receive, send)
