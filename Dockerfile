FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# storage persistente (db + cache evidenze)
ENV VERDIKT_DB=/data/verdikt.db
VOLUME ["/data"]

EXPOSE 8400
CMD ["python", "api.py"]
