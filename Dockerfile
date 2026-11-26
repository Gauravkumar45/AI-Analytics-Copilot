# Single-container image for Hugging Face Spaces (Docker SDK) or any host.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=user . .
RUN python scripts/seed_demo_db.py && mkdir -p reports && chown -R user:user /home/user/app

USER user
ENV HOME=/home/user BACKEND_URL=http://127.0.0.1:8002
EXPOSE 7860
CMD ["./start.sh"]
