# 🏥 Health Insurance Claim Processing System

A comprehensive NoSQL database project implementing a Health Insurance Claim Processing System using **MongoDB**.

## 📋 Project Overview
- **Subject**: Database Management Systems
- **Database**: MongoDB (NoSQL Document Database)
- **Language**: Python 3.x with pymongo
- **Frontend**: FastAPI + Chart.js Web Dashboard

## 🏗️ System Architecture
### Collections (6)
| Collection | Purpose | Records |
|---|---|---|
| patients | Patient demographics | 15 |
| insurers | Insurance companies | 5 |
| policies | Insurance policies | 15 |
| hospitals | Empaneled hospitals | 6 |
| doctors | Treating physicians | 10 |
| claims | Insurance claims | 60 |
| **Total** | | **111** |

### Entity Relationships
- patients ←(1:N)→ policies ←(1:N)→ claims
- insurers ←(1:N)→ policies
- hospitals ←(1:N)→ doctors
- claims → patients, policies, hospitals, doctors

## 🚀 Quick Start
### Prerequisites
- Python 3.8+
- MongoDB (local or Atlas) OR mongomock for offline mode

### Installation
```bash
git clone <repo-url>
cd Database_project
pip install pymongo mongomock tabulate colorama fastapi uvicorn jinja2
```

### Run Interactive CLI (Review Demo)
```bash
cd python_app
python main_cli.py
```

### Run Web Dashboard
```bash
cd web_dashboard
python app.py
# Open http://localhost:8000
```

### Using MongoDB Shell Scripts
```bash
mongosh < scripts/01_schema_validation.js
mongosh < scripts/02_seed_data.js
mongosh < scripts/03_crud_operations.js
mongosh < scripts/04_aggregations.js
mongosh < scripts/05_indexing.js
mongosh < scripts/06_transactions.js
```

## 📊 Features Implemented
### CRUD Operations
- Create: Register patients, create policies, submit claims
- Read: Search, filter, $lookup joins, text search
- Update: Status transitions with audit trail ($push to status_history)
- Delete: Soft-delete (cancel) and hard delete

### Advanced NoSQL Features
- **Aggregation Pipelines**: 5 complex business intelligence queries
- **Indexing**: Single, compound, multikey, text indexes with performance benchmarking
- **ACID Transactions**: Multi-document transaction for claim settlement
- **Schema Validation**: $jsonSchema validators on collections

## 📁 Project Structure
```
Database_project/
├── python_app/
│   ├── db_config.py
│   ├── main_cli.py
│   ├── models/
│   └── views/
├── web_dashboard/
│   ├── app.py
│   ├── __init__.py
│   └── templates/
│       └── index.html
├── scripts/
│   ├── 01_schema_validation.js
│   ├── 02_seed_data.js
│   └── ...
├── README.md
├── Review_2_Presentation_Guide.md
└── .gitignore
```

## 👥 Team
- Nirupam

## 📄 License
MIT
