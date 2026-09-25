# Review 2 Presentation Guide: Health Insurance Claim Processing System

## Demo Script (10-15 minutes)

**1. Introduction (2 mins)**
- Briefly introduce the project: Health Insurance Claim Processing System.
- State the tech stack: MongoDB, Python (PyMongo), FastAPI for Dashboard.
- Show the Entity Relationship summary and collection counts.

**2. Web Dashboard Demo (3 mins)**
- Run `python app.py` and open `http://localhost:8000`.
- Show the visual representation of data: Claims total, patients, status breakdown.
- Highlight the Chart.js visualisations.
- Explain how this provides a real-time BI view for insurance administrators.

**3. CLI Application Demo (5 mins)**
- Run `python main_cli.py`.
- **CRUD Operations**:
  - Show creating a new claim.
  - Show reading/searching a claim.
  - Show updating a claim status.
  - Show deleting/cancelling a claim.
- **Aggregations**:
  - Run the aggregation pipelines from the CLI. Show how it calculates claim approval rates or hospital performance.
- **Transactions**:
  - Trigger a claim settlement. Explain how it updates both the claim status and the policy coverage amount atomically.

**4. Code Walkthrough (3 mins)**
- Show `01_schema_validation.js` to highlight MongoDB `$jsonSchema`.
- Show `04_aggregations.js` to highlight complex pipelines (unwind, group, lookup).
- Show index creation in `05_indexing.js`.

**5. Q&A Preparation (2 mins)**
- Keep answers concise. Refer to the common viva questions below.

---

## Rubric Talking Points (2+3+5 marks)

- **Schema Design (2 marks)**: Explain document model choices (embedding vs referencing). We embedded status history inside claims (array) but referenced patients and hospitals to avoid data duplication.
- **Query Optimization (3 marks)**: Show single, compound, and text indexes. Explain how compound indexes on `(status, claim_date)` speed up the dashboard queries.
- **Advanced Features (5 marks)**: Focus on Aggregations, Schema Validation (`$jsonSchema`), and Multi-document ACID Transactions using PyMongo.

---

## Common Viva Questions

**Q1: Why NoSQL (MongoDB) for this project?**
*Answer*: Claims data is highly variable. Some claims have extensive medical codes, others have few. MongoDB's flexible schema handles this well. It also natively supports embedding arrays like status histories or itemized bills directly inside the claim document.

**Q2: How did you handle joins in MongoDB?**
*Answer*: Using the `$lookup` aggregation stage. For example, to join a claim with patient details and hospital details to generate a comprehensive report.

**Q3: What indexes did you use?**
*Answer*: We used a compound index on `status` and `claim_date` for dashboard filtering, a multikey index on arrays if applicable, and a text index for searching claim descriptions/diagnoses.

**Q4: Explain your aggregation pipeline.**
*Answer*: Our main pipeline uses `$match` to filter claims, `$group` to aggregate amounts by status or hospital, and `$sort` to order the results. We also use `$unwind` when dealing with arrays inside documents before grouping.

**Q5: How do transactions work in MongoDB?**
*Answer*: MongoDB supports multi-document ACID transactions. We use them for claim settlement, where we must update the claim's status to 'Settled' AND deduct the amount from the patient's policy remaining coverage simultaneously. If one fails, the transaction aborts.

**Q6: What is $jsonSchema?**
*Answer*: It is MongoDB's built-in schema validation tool. We use it to ensure required fields (like `claim_amount`, `patient_id`) are present and of the correct type (e.g., amount must be a number > 0) before a document is inserted.
