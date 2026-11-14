# Takumi Project ATMA - Task 3: Build Readiness Checker

## 1. How to Run It (2 Commands)

Run these commands inside the project folder:

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Once the server starts, open **http://127.0.0.1:8000/docs** in your browser to test all endpoints interactively.

---

## 2. What I Finished vs. What I Skipped (and Why)

### What I Finished:
- **LangGraph `StateGraph` Workflow**: Implemented all 5 required plain-Python nodes (`load_parts`, `check_bom`, `substitute`, `reserve`, `save`) with conditional edges and a maximum loop limit of 3 iterations.
- **BOM Validation Checks (S1 & S2)**:
  - **S1 (Stock Check)**: Verifies `stock_qty >= qty_per_vehicle * quantity`.
  - **S2 (Voltage Check)**: Verifies `part.voltage == system_voltage`.
- **Priority-Based Alternate Swapping**: Automatically swaps failing parts (`CTL-48-80` -> `CTL-60-80` -> `CTL-48-80-B`), logs the substitution reason and iteration number, and loops back to `check_bom` to validate the replacement part.
- **All 5 FastAPI Endpoints**:
  - `POST /builds` (runs the LangGraph workflow and returns the saved build without `created_at`)
  - `GET /builds/{id}` (returns a specific saved build)
  - `GET /builds?status=blocked` (lists builds with status filtering and pagination)
  - `GET /parts` (returns all parts and their current stock)
  - `PUT /parts/{part_no}/stock` (updates stock quantity for a part)
- **Input Validation**: Used Pydantic models (`Field(gt=0)`) to automatically return `422` when `quantity <= 0` and `404` when an unknown part number is requested.

### What I Skipped and Why:
- **PostgreSQL, Redis & Bonus Items (Docker Compose, Alembic, pytest)**: Following Rule 8 (*"A working, simple solution beats an unfinished, complex one"*), I used in-memory Python dictionaries and lists instead of external PostgreSQL/Redis database containers. This allowed me to focus on getting the core LangGraph state transitions, substitution loop, and FastAPI endpoints 100% working and easy to verify within the time limit without requiring any external database credentials or `.env` secrets.

---

## 3. Reproduce the Worked Example (cURL Request & Response)

### cURL Request:
```bash
curl -X POST http://127.0.0.1:8000/builds \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_model": "3w_cargo",
    "system_voltage": 48,
    "quantity": 10,
    "bom": [
      {"part_no": "MTR-48-2K", "qty_per_vehicle": 1},
      {"part_no": "CTL-48-80", "qty_per_vehicle": 1},
      {"part_no": "BAT-48-100", "qty_per_vehicle": 1},
      {"part_no": "DCDC-48-12", "qty_per_vehicle": 1}
    ]
  }'
```

### Response:
```json
{
  "id": 1,
  "vehicle_model": "3w_cargo",
  "system_voltage": 48,
  "quantity": 10,
  "bom": [
    {"part_no": "MTR-48-2K", "qty_per_vehicle": 1},
    {"part_no": "CTL-48-80", "qty_per_vehicle": 1},
    {"part_no": "BAT-48-100", "qty_per_vehicle": 1},
    {"part_no": "DCDC-48-12", "qty_per_vehicle": 1}
  ],
  "final_bom": [
    "MTR-48-2K",
    "CTL-48-80-B",
    "BAT-48-100",
    "DCDC-48-12"
  ],
  "substitutions": [
    {
      "iteration": 1,
      "from": "CTL-48-80",
      "to": "CTL-60-80",
      "reason": "S1: need 10, stock 5"
    },
    {
      "iteration": 2,
      "from": "CTL-60-80",
      "to": "CTL-48-80-B",
      "reason": "S2: 60 V, build is 48 V"
    }
  ],
  "status": "substituted",
  "iterations": 3
}
```