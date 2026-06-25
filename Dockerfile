FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
  libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download the model at BUILD time so startup doesn't block the port bind
RUN python -c "from rembg import new_session; new_session('birefnet-general-lite')"

# Bind Render's $PORT; exec keeps uvicorn as PID 1 so it gets SIGTERM cleanly
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
