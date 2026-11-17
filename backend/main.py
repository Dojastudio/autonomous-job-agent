"""HTTP API only. Scheduling and execution live in backend.worker.run_sweep."""
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from backend.api.routes import router

app = FastAPI(title='Job Aid API',version='2.0.0')
app.include_router(router)

@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logging.getLogger(__name__).exception('Request failed: %s',request.url.path)
    return JSONResponse(status_code=503,content={'detail':'The operation could not be completed. Please retry later.'})
