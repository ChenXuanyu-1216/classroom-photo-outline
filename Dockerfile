FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
WORKDIR /srv/app/lecture-syllabus-generator/backend
COPY lecture-syllabus-generator/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app lecture-syllabus-generator/backend/ ./
COPY --chown=app:app lecture-syllabus-generator/frontend/ ../frontend/
RUN mkdir -p data storage && chown app:app data storage
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health/live',timeout=3)"
CMD ["python", "run.py"]
