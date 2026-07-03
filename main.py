import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

import config

app = FastAPI()

# In-memory stores (single-process deployment).
idempotency_store = {}
q9_rate_limit_store = defaultdict(deque)
q10_rate_limit_store = defaultdict(deque)


def is_rate_limited(store: dict, client_id: str, limit: int, window_seconds: int = 10) -> bool:
    now = time.time()
    history = store[client_id]

    while history and now - history[0] >= window_seconds:
        history.popleft()

    if len(history) >= limit:
        return True

    history.append(now)
    return False


@app.middleware("http")
async def q9_q10_middleware(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    origin = request.headers.get("Origin")
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.req_id = req_id

    if request.method == "OPTIONS":
        response = Response(status_code=204)
    else:
        if path == "/orders":
            client_id = request.headers.get("X-Client-Id", "default")
            if is_rate_limited(q9_rate_limit_store, client_id, config.Q9_RATE_LIMIT):
                response = Response(status_code=429, headers={"Retry-After": "10"})
            else:
                response = await call_next(request)
        elif path == "/ping":
            client_id = request.headers.get("X-Client-Id", "default")
            if is_rate_limited(q10_rate_limit_store, client_id, config.Q10_RATE_LIMIT):
                response = Response(status_code=429, headers={"Retry-After": "10"})
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)

    if origin and path == "/ping":
        if origin == config.Q10_ALLOWED_ORIGIN or config.EXAM_PORTAL_ORIGIN in origin:
            response.headers["Access-Control-Allow-Origin"] = origin
    elif origin and path == "/orders":
        if config.EXAM_PORTAL_ORIGIN in origin:
            response.headers["Access-Control-Allow-Origin"] = origin

    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["X-Request-ID"] = req_id
    return response


@app.post("/orders")
async def create_order(request: Request):
    idempotency_key = request.headers.get("Idempotency-Key")

    if idempotency_key and idempotency_key in idempotency_store:
        return {"id": idempotency_store[idempotency_key]}

    order_id = str(uuid.uuid4())
    if idempotency_key:
        idempotency_store[idempotency_key] = order_id

    return JSONResponse(status_code=201, content={"id": order_id})


@app.get("/orders")
async def get_orders(limit: int = 10, cursor: str = ""):
    if limit < 1:
        limit = 1

    start_idx = int(cursor) if cursor.isdigit() else 0
    all_items = [{"id": i} for i in range(1, config.Q9_TOTAL_ORDERS + 1)]

    end_idx = min(start_idx + limit, len(all_items))
    page = all_items[start_idx:end_idx]
    next_cursor = str(end_idx) if end_idx < len(all_items) else None

    return {"items": page, "next_cursor": next_cursor}


@app.get("/ping")
async def ping(request: Request):
    return {"email": config.EMAIL, "request_id": request.state.req_id}
