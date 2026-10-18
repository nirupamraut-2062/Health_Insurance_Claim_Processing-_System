# ClaimSure: Health Insurance Claim Processing & Analytics System

ClaimSure is a NoSQL (MongoDB) system that runs the full life of a health insurance claim:

1. Intimation and pre-authorisation.
2. Document checks and adjudication against the policy wording.
3. Approval limits and escalation.
4. Settlement as a multi-document ACID transaction.
5. Fraud scoring, grievances and an audit trail.

On top of this sits an analytics layer of **19 aggregation pipelines**, and a set of database labs that demonstrate concurrency control, indexing at scale, security, change streams, views, schema migration and backup on a real MongoDB server.

The full design rationale is in **[docs/DATABASE_DESIGN.md](docs/DATABASE_DESIGN.md)**: ER model, relational comparison, consistency, sharding, recovery and security.

> **Course:** Database Management Systems &middot; **Database:** MongoDB &middot; **Stack:** Python 3, PyMongo, FastAPI, Jinja2, Chart.js
> All people, hospitals, insurers and companies in the data set are fictional.

![Dashboard](docs/screenshots/dashboard.png)

---

## Highlights

| Area | What is implemented |
|---|---|
| **Data volume** | 16,758 documents in **14 collections**: 5,288 members, 2,200 policies, 2,591 claims, 99 hospitals, 484 doctors, 2,039 payments, 3,631 audit entries; plus a 100,000-document benchmark collection |
| **Schema design** | Embedding, references, the extended-reference, subset and computed patterns, bounded arrays, and a sequence (counter) collection |
| **Schema validation** | `$jsonSchema` validators on 14 collections (required fields, types, enums, regex patterns, min/max), defined once in `schema/validators.json` |
| **Indexes** | 50 secondary indexes (65 with the default `_id` indexes): unique, compound (ESR rule), multikey, **text**, **partial**, **sparse**, **TTL** and **2dsphere** |
| **Aggregation** | `$facet`, `$bucket`, `$lookup`, `$unwind`, `$group`, `$cond`, `$dateToString`; mongosh scripts add `$setWindowFields`, `$dateDiff`, `$percentile`, `$merge` and `$geoNear` |
| **Transactions** | Claim settlement updates `claims`, `policies`, `payments` and `audit_logs` atomically inside `session.with_transaction()` (automatic retry on write conflicts), with a "simulate failure" button that proves the rollback |
| **Concurrency control** | Optimistic locking (`version` field) on every claim action; a lab that shows a lost update, then fixes it with an atomic `$inc`, optimistic locking and a transaction that hits a real `WriteConflict` |
| **Performance at scale** | 100,000-document benchmark with real `explain("executionStats")`: covered queries, the prefix rule, and cases where an index does *not* help |
| **Security** | Database users and roles (least privilege), a custom auditor role, a PII-masked view; the server refuses the analyst's writes |
| **Staff sign-in** | Every page needs a login. scrypt password hashes in a separate `credentials` collection, server-side sessions with a **TTL index**, lock-out after 5 wrong passwords, and three access levels (Head Admin, Supervisor, Staff) enforced by one permission matrix |
| **Re-verification** | Only the Head Admin can reopen a closed case (Settled, Rejected or Withdrawn). A verifier upholds it, sends it for fresh adjudication, or flags a paid claim for recovery |
| **Change streams** | Live feed of inserts and updates pushed to the browser and the CLI |
| **Administration** | Views, a `$merge` materialised view, schema versioning with migration, a bulk-write renewal batch job, backup and restore |
| **Business logic** | Adjudication engine (waiting periods, room-rent proportionate deduction, sub-limits, co-pay, sum-insured cap), approval-limit escalation, an 11-state claim workflow, fraud scoring with 9 red-flag rules |
| **Interfaces** | A web application with 15+ pages, an interactive CLI, 8 mongosh scripts and a REST/JSON API for the charts |
| **Quality** | 108 mock-mode tests plus 32 live tests that run against a real MongoDB replica set (`python main.py test-live`) |

---

## Quick start (no database installation needed)

```bash
pip install -r requirements.txt
python main.py web          # opens http://127.0.0.1:8000
```

By default ClaimSure runs in **mock mode**. It uses `mongomock`, an in-memory MongoDB, and loads the data set at start-up in about 3 seconds. Every feature works in mock mode. Transactions are emulated, validation runs in the application and query plans are simulated, and the UI labels each of these.

Other commands:

```bash
python main.py              # interactive console menu
python main.py report       # KPI summary + insurer / product / rejection reports
python main.py test         # 108 tests on the in-memory mock
python main.py generate     # regenerate data/*.json (deterministic)
```

Database commands (most need a real MongoDB, see below):

```bash
python main.py init-db      # validators + data + indexes + views on the server
python main.py test-live    # 32 live tests in a separate claimsure_test database
python main.py lab          # concurrency lab: lost update vs $inc vs optimistic lock vs transaction
python main.py bench        # 100,000-document index benchmark (--n to change the size)
python main.py watch        # print the change stream live (make changes in the web app)
python main.py security     # try reads and writes as the read-only analyst user
python main.py migrate      # upgrade claims to schema version 2
python main.py renew        # policy renewal batch job
python main.py views        # create the read-only views
python main.py backup       # logical backup to backups/<timestamp>/
python main.py restore NAME # restore a backup
python main.py users        # staff sign-in accounts, access levels, last sign-in
python main.py reset-password USR001   # issue a temporary password (e.g. if the Head Admin is locked out)
```

## Signing in and access levels

Every page needs a sign-in with the work email or the staff ID (`USR001` ... `USR022`). The first time the app starts, it gives each staff member a password:
- `CLAIMSURE_ADMIN_PASSWORD` from `.env` for the Head Admin
- `CLAIMSURE_STAFF_PASSWORD` from `.env` for everyone else

If these are not set, it generates the passwords and prints them once. After that, the Head Admin manages passwords on the **Staff & Access** page.

| Level | Roles | Authority |
|---|---|---|
| **Head Admin** | Admin (Ananya Rao, `USR001`) | Everything, plus: reopen a closed case for re-verification, create and deactivate staff, change roles, reset passwords, reset or restore the database, run maintenance jobs and the benchmark |
| **Supervisor** | Claims Manager | Approve up to ₹10 lakh, re-open a rejected claim after an appeal, conclude any re-verification, review fraud, read the audit log, run the labs |
| **Staff** | Senior Claims Adjuster, Medical Officer, Claims Executive, Fraud Analyst | Work their queue within the role's approval limit (₹3 lakh, ₹2 lakh, ₹75,000, none). Anything above the limit is escalated automatically |

The permission matrix is defined once in `claimsure/auth.py`. The web routes, the workflow services and the templates all read it.

**How it is stored:**
- `credentials` has one document per staff member, linked 1 : 1 to `users` by a unique index. It holds the scrypt hash, failed attempts, lock-out time and last sign-in. A database reset never touches it.
- `sessions` has one document per signed-in browser. The cookie holds a random token and only its SHA-256 digest is stored. A TTL index deletes sessions when they expire after 12 hours.
- Each request resolves its session with one aggregation: `$match`, then `$lookup` into `users` and `credentials`.
- Five wrong passwords lock an account for 15 minutes.
- Every sign-in, failure, lock-out and account change goes to `audit_logs`.

**Reopening a closed case (re-verification).**
- On a Settled, Rejected or Withdrawn claim, the Head Admin chooses **Reopen case for re-verification**, gives a reason and picks a verifier. The claim moves to **Re-verification** and lands in the verifier's queue.
- The verifier, a Claims Manager or the Head Admin then records an outcome:
  - *uphold*: back to the original status
  - *re-adjudicate*: back to Under Review, for unpaid claims only; the old decision is kept in `previous_adjudications`
  - *flag for recovery*: for settled claims; the payment is never made twice
- Each round is an entry in the embedded `reverifications` array.

## Public deployment (Render + Atlas)

The repository includes `render.yaml`, so Render can deploy it as a free public web service. The database stays on Atlas.

1. In Atlas, go to **Network Access** and add `0.0.0.0/0`. Render's servers have no fixed IP address; the database password and TLS still protect it.
2. Sign in at render.com with GitHub, then choose **New → Blueprint** and select this repository.
3. Paste `CLAIMSURE_MONGO_URI` and `CLAIMSURE_ANALYST_URI`, the same values as in `.env`, then click **Deploy**.
4. The site is served at `https://claimsure-xxxx.onrender.com`. Sign in with the staff accounts.

The sign-in passwords already live in Atlas: the app wrote them there the first time it ran locally, so Render needs no password variables. On the free plan the service sleeps after 15 minutes without visitors, and the first request after that takes about 30–50 seconds.

## Running on a real MongoDB (free)

MongoDB is free: use an **Atlas M0** cluster or the **Community Server** locally. Transactions and change streams need a replica set, which Atlas always provides.

### Atlas (recommended)

1. Create a free **M0** cluster at mongodb.com/cloud/atlas (region: Mumbai).
2. **Database Access**: add `claimsure_app` (*Read and write to any database*) and `claimsure_analyst` (*Only read any database*).
3. **Network Access**: add your IP address (or `0.0.0.0/0` for a demo on changing networks).
4. Create a file named `.env` in the project folder. It is git-ignored, so your password never reaches GitHub.

```ini
CLAIMSURE_MONGO_URI=mongodb+srv://claimsure_app:PASSWORD@cluster.xxxxx.mongodb.net/?retryWrites=true&w=majority
CLAIMSURE_ANALYST_URI=mongodb+srv://claimsure_analyst:PASSWORD@cluster.xxxxx.mongodb.net/
CLAIMSURE_ADMIN_PASSWORD=choose-a-head-admin-password1
CLAIMSURE_STAFF_PASSWORD=choose-a-staff-password1
```

```bash
python main.py init-db      # loads everything into Atlas
python main.py test-live    # proves every feature on the server
python main.py web          # header shows "LIVE · MongoDB (Atlas)"
```

Add `CLAIMSURE_MODE=mock` to `.env` (or the environment) to switch back to the in-memory mock at any time.

### Local Community Server

```bash
# Local single-node replica set (one time)
mongod --replSet rs0 --dbpath ./mongo-data
mongosh --eval "rs.initiate()"

# Point ClaimSure at it
set CLAIMSURE_MONGO_URI=mongodb://localhost:27017/?replicaSet=rs0      # Windows
export CLAIMSURE_MONGO_URI="mongodb://localhost:27017/?replicaSet=rs0" # macOS / Linux

python main.py init-db      # validators + data + indexes on the server
python main.py web          # header now shows "LIVE · MongoDB"
```

With a real server the app uses native transactions, server-side `$jsonSchema` validation, true `explain("executionStats")` plans, `$text` search and `$near` geo queries.

### mongosh scripts (`scripts/`)

Run these from the project root, in order:

| Script | Shows |
|---|---|
| `01_create_collections.js` | Collections created with `$jsonSchema` validators read from `schema/validators.json` |
| `02_load_data.js` | Loads `data/*.json` with `EJSON.parse` and initialises the counters |
| `03_indexes.js` | Builds all indexes from `schema/indexes.json` and proves the unique index stops a double payment |
| `04_crud.js` | Create (with an atomic counter), read (`$all`, `$elemMatch`, positional projection, `$lookup`), update (`$push`, `arrayFilters`, upsert), soft and hard delete |
| `05_aggregations.js` | `$facet`, `$setWindowFields` (running totals, moving average, `$rank`), `$dateDiff` with `$percentile`, `$lookup` with a sub-pipeline, `$merge` materialised view |
| `06_indexing_explain.js` | `explain("executionStats")` with and without an index (`hint({$natural:1})`), a covered query, `$indexStats` |
| `07_transactions.js` | Session transaction with commit, a forced abort with verification, and a refused double settlement |
| `08_geo_text_search.js` | `$geoNear` nearest network hospitals, `$geoWithin`, `$text` search with relevance score |
| `09_security.js` | Users, roles and a custom role; a read-only user's write is refused |

```bash
mongosh "mongodb://localhost:27017/?replicaSet=rs0" --file scripts/01_create_collections.js
```

`scripts/09_security.js` creates the users and a custom auditor role, and shows a refused write (self-managed servers; on Atlas use Database Access).

All nine scripts have been run against MongoDB 8.0 (single-node replica set).

---

## Data model

```mermaid
erDiagram
    INSURERS ||--o{ PLANS : offers
    INSURERS }o--o{ TPAS : "outsources to"
    PLANS ||--o{ POLICIES : "sold as"
    AGENTS ||--o{ POLICIES : sells
    MEMBERS }o--o{ POLICIES : "insured under (embedded subset)"
    POLICIES ||--o{ CLAIMS : "claimed on"
    MEMBERS ||--o{ CLAIMS : "patient of"
    HOSPITALS ||--o{ DOCTORS : employs
    HOSPITALS ||--o{ CLAIMS : treats
    ICD_CODES ||--o{ CLAIMS : "diagnosis (multikey)"
    CLAIMS ||--o| PAYMENTS : "settled by (unique)"
    CLAIMS ||--o{ GRIEVANCES : "disputed in"
    USERS ||--o{ CLAIMS : adjudicates
```

| Collection | Docs | Purpose | Notable modelling |
|---|---:|---|---|
| `insurers` | 10 | Insurance companies | IRDAI registration, solvency ratio |
| `tpas` | 6 | Third-party administrators | Many-to-many with insurers (id arrays) |
| `plans` | 35 | Products (Basic, Comprehensive, Senior, Premier, Group) | Embedded features, waiting periods, sub-limits |
| `icd_codes` | 55 | ICD-10 diagnosis master | Benchmark cost and stay used by fraud rules |
| `hospitals` | 99 | Hospitals across 26 cities | GeoJSON `location` (2dsphere), network insurer array |
| `doctors` | 484 | Treating doctors | Unique medical registration number |
| `members` | 5,288 | Insured people | KYC (masked), lifestyle, declared pre-existing conditions |
| `policies` | 2,200 | Policies in force or expired | **Embedded** `insured_members[]` (subset) and `renewal_history[]` |
| `claims` | 2,591 | The claim case file | Embedded bill, diagnosis, documents, queries, `status_history[]`, adjudication and fraud; `snapshot` extended reference |
| `payments` | 2,039 | Settlements | Unique on `claim_number`, so a claim can't be paid twice |
| `grievances` | 253 | Customer complaints | Linked to claims |
| `audit_logs` | 3,631 | Append-only change log | TTL index (3-year retention) |
| `users` / `agents` | 22 / 45 | Staff with approval limits; sales intermediaries | |

### Why embed or reference?

- **Embed** what is always read and written with its parent and is bounded in size. Examples are a claim's bill, deductions and status history, and a policy's insured members and renewal years. One read loads the whole case file, and one atomic update changes status and history together.
- **Reference** shared or unbounded data: members, hospitals, policies and payments.
- **Extended reference**: claims copy the patient name, age, hospital name, city and tier into `snapshot`. Lists render without `$lookup`, and history stays correct if the master record changes later.

---

## Claim lifecycle

```mermaid
stateDiagram-v2
    [*] --> Intimated
    Intimated --> DocumentsPending
    Intimated --> UnderReview
    DocumentsPending --> UnderReview
    UnderReview --> QueryRaised
    QueryRaised --> UnderReview
    UnderReview --> Escalated: payable > approval limit
    UnderReview --> Approved
    UnderReview --> PartiallyApproved
    UnderReview --> Rejected
    Escalated --> Approved
    Escalated --> PartiallyApproved
    Escalated --> Rejected
    Approved --> Settled: ACID transaction
    PartiallyApproved --> Settled: ACID transaction
    Rejected --> UnderReview: re-opened (manager)
    Intimated --> Withdrawn
    Settled --> Reverification: reopened (Head Admin)
    Rejected --> Reverification: reopened (Head Admin)
    Withdrawn --> Reverification: reopened (Head Admin)
    Reverification --> Settled: upheld / recovery flagged
    Reverification --> Rejected: upheld
    Reverification --> Withdrawn: upheld
    Reverification --> UnderReview: re-adjudicate (unpaid only)
    Settled --> [*]
```

### Adjudication engine (`claimsure/adjudication.py`)

1. **Eligibility:**
   - policy in force on the admission date
   - patient is an insured member
   - initial 30-day waiting period (accidents exempt)
   - maternity cover and waiting period
   - specific-illness waiting period (24 months)
   - pre-existing disease waiting period
   - exclusions
   - reviewer rejection (documents missing, fraud, not medically necessary)
2. **Financials:**
   - non-payable consumables and admin charges
   - room-rent cap (1% of sum insured or a room category), with the IRDAI **proportionate deduction** on associated charges
   - ambulance limit
   - disease sub-limits (e.g. cataract ₹40,000) and the maternity limit
   - co-payment (20% on senior plans)
   - available sum insured

The data generator uses the same engine, so every seeded amount is consistent.

### Fraud engine (`claimsure/fraud.py`)

Nine red-flag rules produce a 0–100 score (high risk is 60 or more):
- duplicate or overlapping stay
- inflated bill against the ICD benchmark
- early claim after inception
- frequent claimant
- watch-listed hospital
- inconsistent length of stay
- out-of-state planned treatment
- high-value non-network claim
- round-figure bill

The scan builds its context with bulk aggregation, not one query per claim.

---

## Project structure

```
claimsure/            core package (no web code)
  db.py               mock / real MongoDB connection
  schema.py           $jsonSchema loader + validator (mock mode)
  indexes.py          index creation, catalogue, explain-based benchmark
  seed.py             loads data/*.json, counters, indexes
  adjudication.py     policy rules engine
  fraud.py            fraud scoring
  services.py         workflow: intimation, status changes, decisions, settlement, re-verification, grievances
  auth.py             staff sign-in, sessions, lock-out, access levels and the permission matrix
  transactions.py     with_transaction() with retry / emulated rollback in mock mode
  concurrency.py      concurrency-control lab (lost update, $inc, optimistic lock, transaction)
  benchmark.py        100,000-document index benchmark with explain()
  security.py         role design and the read-only analyst test
  livefeed.py         change streams -> Server-Sent Events
  maintenance.py      renewal batch, schema migration, views, materialised view, backup/restore
  analytics.py        19 aggregation-pipeline reports + $facet KPIs
  audit.py, constants.py, utils.py
web/                  FastAPI app, Jinja2 templates, CSS/JS, bundled Chart.js
schema/               validators.json, indexes.json  (shared by Python and mongosh)
data/                 the data set (MongoDB Extended JSON)
scripts/              mongosh scripts 01-09
tools/generate_data.py  deterministic data generator
tests/                pytest suite (mock mode)
tests_live/           tests that need a real MongoDB server
docs/                 DATABASE_DESIGN.md, presentation, screenshots
main.py               CLI entry point
```

## Web application pages

| Page | What it does |
|---|---|
| Dashboard | KPIs (settlement ratio, turnaround time, open fraud alerts), charts, your work queue, claims awaiting payment |
| Claims | Filter, search, sort, paginate and export to CSV; shows the MongoDB query behind the page |
| Claim detail | Patient and policy, adjudication worksheet with eligibility checks and deductions, role-aware actions, fraud assessment, timeline, payment, audit |
| New claim | Policy lookup, network check, bill entry; validated, fraud-scored and audited |
| Policies / Members / Hospitals | Master data with drill-downs; nearest hospitals use `$near` (MongoDB) or haversine (mock) |
| Analytics | 19 reports, each with chart, data table and the exact aggregation pipeline |
| Fraud watch | Risk-ranked alerts, rule statistics, watch-listed hospitals, re-scan |
| Grievances / Audit log | Complaint handling and the full change history |
| DB console | Collections and sizes, data model, design patterns, index catalogue, validators, live demos |
| Concurrency lab | Four strategies for two simultaneous payments on one policy, with an event timeline |
| Scale benchmark | 100,000 documents; plans, keys and docs examined, and times before and after indexing |
| Security & roles | Role design; live test of the read-only analyst account |
| Live feed | Change stream events as they happen |
| Maintenance | Renewal batch, schema migration, views, materialised view, backup and restore |
| Query playground | Read-only `find` and `aggregate` with ready-made examples (no lookups into `credentials` or `sessions`) |
| Sign-in | Email or staff ID and password; lock-out after 5 failures |
| Staff & access | Head Admin only: accounts, roles, regions, temporary passwords, unlock, sign-out, permission matrix, security events |
| My account | Access level, what you may do, change password, your sessions and sign-in history |

### Database labs (on a real MongoDB server)

![Concurrency lab](docs/screenshots/concurrency_lab.png)

![Scale benchmark](docs/screenshots/scale_benchmark.png)

## Testing

```bash
python main.py test              # 108 tests on the in-memory mock
python main.py test-live         # 32 tests on the real MongoDB in .env (separate claimsure_test database)
```

## Team

- Nirupam

## License

MIT
