#!/usr/bin/env python3
"""Web server entry point. Run: python web_server.py"""
import os
import uvicorn

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(
        "web_app.app:app",
        host=host,
        port=port,
        workers=1,
        log_level="info",
    )
