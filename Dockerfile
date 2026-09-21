# The bookkeeping app as one image. The SAME image runs once per company; each
# instance is told which company it is through its own env file (DB_NAME,
# WhatsApp number, business details, owner allowlist).
FROM python:3.12-slim

WORKDIR /app

# Runtime libraries Pillow/reportlab use for images in the invoice PDFs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cached unless requirements.txt changes).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then the application code.
COPY . .

# On start: make sure THIS company's database + tables exist (safe to re-run),
# then serve the webhook. Caddy reaches this on port 8000 over the private network.
CMD ["sh", "-c", "python init_db.py && uvicorn webhook:app --host 0.0.0.0 --port 8000"]
