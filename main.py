import os
from contextlib import asynccontextmanager
from io import BytesIO

from fastapi import FastAPI, BackgroundTasks, Depends
from pydantic import BaseModel
from PIL import Image
from rembg import remove, new_session
from supabase import create_client, Client

from auth import verify_user

SUPABASE_URL = os.environ["SUPABASE_URL"]
SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
ORIGINALS = "originals"
CUTOUTS = "cutouts"

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["session"] = new_session("birefnet-general-lite")  # warm the model once
    state["supabase"] = create_client(SUPABASE_URL, SECRET_KEY)
    yield
    state.clear()


app = FastAPI(lifespan=lifespan)


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


def run_cutout(item_id: str, user_id: str):
    sb: Client = state["supabase"]

    # Service key bypasses RLS, so confirm ownership ourselves before touching anything.
    row = (
        sb.table("clothing_items")
        .select("user_id, original_path")
        .eq("id", item_id)
        .single()
        .execute()
    )
    if not row.data or row.data["user_id"] != user_id:
        return

    original_path = row.data["original_path"]
    try:
        original_bytes = sb.storage.from_(ORIGINALS).download(original_path)

        img = Image.open(BytesIO(original_bytes))
        out = remove(img, session=state["session"])
        buf = BytesIO()
        out.save(buf, format="PNG")

        cutout_path = f"{user_id}/{item_id}.png"
        sb.storage.from_(CUTOUTS).upload(
            cutout_path,
            buf.getvalue(),
            file_options={"content-type": "image/png", "upsert": "true"},
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
