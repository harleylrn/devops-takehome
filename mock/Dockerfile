FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY takehome ./takehome
USER 10001:10001
EXPOSE 8081
CMD ["python", "-m", "takehome", "mock"]
