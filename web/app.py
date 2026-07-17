import os
import asyncio
import subprocess
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

app = FastAPI()

# Ensure static directory exists relative to app.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class AnalyzeRequest(BaseModel):
    target: str

jobs = {}

@app.get("/")
async def root():
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    target = req.target.strip().lstrip("@")
    job_id = f"job_{target}"
    
    if job_id in jobs and jobs[job_id].poll() is None:
        return {"job_id": job_id, "status": "already_running"}
    
    import sys
    # Inicia processo do Nimrod usando o mesmo executável python do servidor
    # Bufsize 1 for line buffering
    process = subprocess.Popen(
        [sys.executable, "nimrod.py", f"@{target}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, # Redirect stderr to stdout so we get everything in one stream
        text=True,
        bufsize=1,
        cwd=os.path.dirname(BASE_DIR)
    )
    
    jobs[job_id] = process
    return {"job_id": job_id, "status": "started"}

@app.get("/api/stream/{job_id}")
async def stream_logs(request: Request, job_id: str):
    process = jobs.get(job_id)
    if not process:
        return {"error": "Job not found"}

    async def event_generator():
        # Avoid blocking the event loop by running readline in executor
        loop = asyncio.get_event_loop()
        while True:
            if await request.is_disconnected():
                break
            
            line = await loop.run_in_executor(None, process.stdout.readline)
            if line:
                yield {"data": line.strip()}
            else:
                if process.poll() is not None:
                    # Emit one final message indicating end
                    yield {"data": "[END_OF_STREAM]"}
                    break
                await asyncio.sleep(0.1)

    return EventSourceResponse(event_generator())
