import os
from contextlib import asynccontextmanager
from io import BytesIO
import asyncio

from fastapi import FastAPI, BackgroundTasks, Depends
from pydantic import BaseModel
from PIL import Image
from rembg import remove, new_session
from supabase import create_client, Client
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from telemetry import setup_telemetry
from opentelemetry import metrics, trace

from auth import verify_user

SUPABASE_URL = os.environ["SUPABASE_URL"]
SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
ORIGINALS = "originals"
CUTOUTS = "cutouts"

setup_telemetry()

state: dict = {}

_meter = metrics.get_meter("drestly.inference")
_queued = _meter.create_up_down_counter(
    "inference.queue.depth",
    unit="{request}",
    description="Requests waiting to acquire an inference slot",
)
_active = _meter.create_up_down_counter(
    "inference.active",
    unit="{request}",
    description="Requests currently running inference",
)

INFERENCE_SLOTS = asyncio.Semaphore(2)


@asynccontextmanager
async def inference_slot():
    _queued.add(1)  # entered the line
    try:
        await INFERENCE_SLOTS.acquire()
    finally:
        _queued.add(-1)  # left the line (got in, or was cancelled)
    _active.add(1)  # running
    try:
        yield
    finally:
        _active.add(-1)
        INFERENCE_SLOTS.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["session"] = new_session("birefnet-general-lite")  # warm the model once
    state["supabase"] = create_client(SUPABASE_URL, SECRET_KEY)
    yield
    # Flush buffered spans/metrics before the process exits (BatchSpanProcessor
    # and the 60s metric reader would otherwise drop whatever is still queued).
    trace.get_tracer_provider().shutdown()
    metrics.get_meter_provider().shutdown()
    state.clear()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
FastAPIInstrumentor.instrument_app(app)


class ProcessRequest(BaseModel):
    item_id: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/process", status_code=202)
def process(
    req: ProcessRequest,
    background: BackgroundTasks,
    user_id: str = Depends(verify_user),
):
    background.add_task(run_cutout, req.item_id, user_id)
    return {"status": "processing", "item_id": req.item_id}


async def run_cutout(item_id: str, user_id: str):
    tracer = trace.get_tracer("drestly.inference")
    with tracer.start_as_current_span("run_cutout") as span:
        span.set_attribute("item.id", item_id)
        async with inference_slot():
            # rembg, Pillow, and the sync Supabase client all block; run them off the
            # event loop so /health pings and other requests stay responsive.
            await asyncio.to_thread(_do_cutout_blocking, item_id, user_id)


def _do_cutout_blocking(item_id: str, user_id: str):
    sb: Client = state["supabase"]

    # Service key bypasses RLS, so confirm ownership ourselves before touching anything.
    # maybe_single() yields data=None for a missing row instead of raising (unlike single()).
    row = (
        sb.table("clothing_items")
        .select("user_id, original_path")
        .eq("id", item_id)
        .maybe_single()
        .execute()
    )
    if not row or not isinstance(row.data, dict) or row.data["user_id"] != user_id:
        return

    original_path = row.data["original_path"]
    try:
        original_bytes = sb.storage.from_(ORIGINALS).download(original_path)

        img = Image.open(BytesIO(original_bytes))
        out = remove(img, session=state["session"])
        out.thumbnail((1500, 1500))  # cap resolution: in-place, preserves aspect, only downscales
        buf = BytesIO()
        out.save(buf, format="WEBP", quality=80, method=6)

        cutout_path = f"{user_id}/{item_id}.webp"
        sb.storage.from_(CUTOUTS).upload(
            cutout_path,
            buf.getvalue(),
            file_options={"content-type": "image/webp", "upsert": "true"},
        )

        buf.close()

        sb.table("clothing_items").update(
            {
                "status": "done",
                "cutout_path": cutout_path,
            }
        ).eq("id", item_id).execute()

    except Exception as e:
        sb.table("clothing_items").update(
            {
                "status": "failed",
                "error": str(e)[:500],
            }
        ).eq("id", item_id).execute()
        return

    try:
        sb.storage.from_(ORIGINALS).remove([original_path])
    except Exception:
        pass
