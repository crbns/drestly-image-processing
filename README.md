---
title: Image Processing
emoji: 👕
colorFrom: red
colorTo: yellow
sdk: docker
app_port: 7860
---

# Image Processing

Background-removal API for clothing items. A FastAPI service that pulls an
uploaded image from Supabase Storage, runs [`rembg`](https://github.com/danielgatis/rembg)
(BiRefNet `birefnet-general-lite`) to cut out the garment, writes the
transparent PNG back to storage, and updates the item row.

## Endpoints

| Method | Path       | Auth          | Description                                  |
| ------ | ---------- | ------------- | -------------------------------------------- |
| `GET`  | `/health`  | none          | Liveness check, returns `{"ok": true}`.      |
| `POST` | `/process` | Bearer (JWT)  | Queues a cutout job for `item_id`, returns `202`. |

`/process` validates the caller's Supabase JWT (`auth.py`), confirms the user
owns the `clothing_items` row, then runs the cutout in a background task.

## Environment variables

| Variable              | Description                                           |
| --------------------- | ----------------------------------------------------- |
| `SUPABASE_URL`        | Supabase project URL (also used for JWKS auth).       |
| `SUPABASE_SECRET_KEY` | Service-role key — bypasses RLS, ownership checked in code. |

## Running locally

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 7860
```

The container (`Dockerfile`) installs deps, pre-downloads the BiRefNet model,
and serves uvicorn on port `7860` — the default Space port.
