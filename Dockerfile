FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mvp/ ./mvp/

EXPOSE 3300

CMD ["uvicorn", "mvp.app:app", "--host", "0.0.0.0", "--port", "3300"]
