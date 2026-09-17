FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock README.md ./
COPY src ./src

RUN poetry install --only main --no-interaction --no-ansi

EXPOSE 8000

CMD ["uvicorn", "bike_routing_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
