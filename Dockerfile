FROM python:3.11-slim

WORKDIR /app

# Abhängigkeiten zuerst (besseres Layer-Caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 5757

CMD ["python3", "-u", "server.py"]
