FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends fonts-dejavu-core >/dev/null && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt
COPY publicbank ./publicbank
COPY skills ./skills
COPY manifest.json .
CMD exec uvicorn publicbank.main:app --host 0.0.0.0 --port ${PORT}
