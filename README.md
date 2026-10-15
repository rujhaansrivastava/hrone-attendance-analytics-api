# Employee Attendance & Analytics API

FastAPI + PyMongo implementation for the HROne Software Engineer Trainee assignment.

## Setup

Requires Python 3.11+ and MongoDB 6.0+.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Set `MONGO_URI` and `MONGO_DB` in your shell or a local `.env` file (do not commit `.env`). Real environment variables take precedence. For local MongoDB, for example:

```bash
docker run --rm -d --name attendance-mongo -p 27017:27017 mongo:7
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```

`GET /health` pings MongoDB. Interactive API docs are available at `/docs`.

## Implemented

- Employee create/list with validation, pagination, and startup indexes
- Punch-in/punch-out with timestamp validation and attendance business rules
- Manual regularization with change history and optimistic concurrency check
- Employee monthly summary, department summary, late leaderboard, daily department trend
- Explain endpoint for the supported endpoint names

## Notes / limitations

Analytics are implemented as MongoDB aggregation pipelines. The assignment's hidden data includes edge cases beyond the provided sample, so run your own tests for overnight shifts, exact rounding boundaries, missing records, ties, weekends, and join dates. The explain endpoint should be verified against your MongoDB version and actual query plans before submission. Do not commit secrets, `.env`, a Dockerfile, or a virtual environment.
