FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py store.py agent.py ./
COPY web ./web
ENV SHOPBOT_REQUIRE_POSTGRES=1
EXPOSE 7860
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port \"${PORT:-7860}\""]
