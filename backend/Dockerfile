FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY smartdiary ./smartdiary
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN useradd --create-home diary && mkdir /app/.data && chown diary:diary /app/.data
USER diary
EXPOSE 8000
CMD ["uvicorn", "smartdiary.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
