## Requirements

- Python 3.11+
- MongoDB 6.0+ (MongoDB Atlas or local MongoDB)
- Git

## Setup and Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/rujhaansrivastava/hrone-attendance-analytics-api.git
cd hrone-attendance-analytics-api
```

### 2. Create and activate a virtual environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure MongoDB

This application supports MongoDB Atlas and local MongoDB.

Set the following environment variables before starting the application:

- `MONGO_URI`: MongoDB connection string
- `MONGO_DB`: Database name (default: `attendance_db`)

For MongoDB Atlas, obtain the connection string from your Atlas cluster and use it as `MONGO_URI`. Ensure your database user and network access are configured correctly.

For local MongoDB, use:

```text
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Alternatively, configure these variables in a local `.env` file. Do not commit `.env` or database credentials to GitHub. Existing shell environment variables take precedence over values loaded from `.env`.

### 5. Load sample data

After configuring MongoDB, run:

```bash
python sample_seed.py
```

**Note:** The seed script clears and reloads the sample `employees` and `attendance_logs` collections. Use it only when resetting those sample collections is acceptable.

### 6. Start the API

```bash
uvicorn app.main:app --reload --port 8000
```

The API will be available at:

- Interactive API documentation: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`

The health endpoint checks connectivity to MongoDB.

## Implemented Features

- Employee creation and listing with validation, pagination, and startup indexes
- Punch-in and punch-out with timestamp validation and attendance business rules
- Manual attendance regularization with change history and optimistic concurrency checks
- Employee monthly attendance summary
- Department attendance summary
- Late-arrival leaderboard
- Daily department attendance trend
- Query explain endpoint for supported endpoint names

## Notes and Limitations

- Analytics use MongoDB aggregation pipelines.
- Additional testing is recommended for overnight shifts, rounding boundaries, missing records, ranking ties, weekends, and employee joining dates.
- The explain endpoint should be verified against the target MongoDB version and actual query plans.
- Do not commit secrets, `.env` files, virtual environments, or a Dockerfile.
