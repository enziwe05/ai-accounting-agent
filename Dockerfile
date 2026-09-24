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

# On start: make sure THIS company's database + tables exist, then load the
# starter categories + rules (both steps are safe to re-run — they skip anything
# that already exists), then serve the webhook. Without the seed step a brand-new
# company has zero categories, so the bot can't file anything until it's run.
# Caddy reaches this on port 8000 over the private network.
CMD ["sh", "-c", "python init_db.py && python seed_db.py && uvicorn webhook:app --host 0.0.0.0 --port 8000"]
