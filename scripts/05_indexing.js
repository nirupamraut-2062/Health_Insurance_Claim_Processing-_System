// Indexing Strategy & Performance Benchmarking
use('health_insurance_db');

print('\n===== BEFORE INDEXING: Query Performance =====');

// Benchmark 1: Query without index
print('\n--- Query: Find claims by patient P001 (NO INDEX) ---');
var beforePatient = db.claims.find({ patient_id: 'P001' }).explain('executionStats');
print('Execution time: ' + beforePatient.executionStats.executionTimeMillis + 'ms');
print('Documents examined: ' + beforePatient.executionStats.totalDocsExamined);
print('Documents returned: ' + beforePatient.executionStats.nReturned);
print('Scan type: ' + beforePatient.executionStats.executionStages.stage);

// ===== CREATE INDEXES =====
print('\n===== CREATING INDEXES =====');

// 1. Single-field index on status
db.claims.createIndex({ status: 1 });
print('✅ Created single-field index on claims.status');

// 2. Single-field index on claim_date
db.claims.createIndex({ claim_date: -1 });
print('✅ Created single-field index on claims.claim_date (descending)');

// 3. Compound index on patient_id + claim_date
db.claims.createIndex({ patient_id: 1, claim_date: -1 });
print('✅ Created compound index on claims.{patient_id, claim_date}');

// 4. Multikey index on diagnosis array
db.claims.createIndex({ 'diagnosis.icd_code': 1 });
print('✅ Created multikey index on claims.diagnosis.icd_code');

// 5. Text index on treatment description
db.claims.createIndex({ 'treatment_details.description': 'text' });
print('✅ Created text index on claims.treatment_details.description');

// 6. Index on policy_id for lookups
db.claims.createIndex({ policy_id: 1 });
print('✅ Created index on claims.policy_id');

print('\n===== AFTER INDEXING: Query Performance =====');

// Benchmark 2: Same query WITH index
print('\n--- Query: Find claims by patient P001 (WITH INDEX) ---');
var afterPatient = db.claims.find({ patient_id: 'P001' }).explain('executionStats');
print('Execution time: ' + afterPatient.executionStats.executionTimeMillis + 'ms');
print('Documents examined: ' + afterPatient.executionStats.totalDocsExamined);
print('Documents returned: ' + afterPatient.executionStats.nReturned);
print('Scan type: ' + afterPatient.executionStats.executionStages.stage);

// Benchmark 3: Status-based query
print('\n--- Query: Find Rejected claims (WITH INDEX) ---');
var statusQuery = db.claims.find({ status: 'Rejected' }).explain('executionStats');
print('Documents examined: ' + statusQuery.executionStats.totalDocsExamined);
print('Documents returned: ' + statusQuery.executionStats.nReturned);
print('Scan type: ' + statusQuery.executionStats.executionStages.stage);

// Benchmark 4: Multikey index on diagnosis
print('\n--- Query: Find claims with ICD code I25.10 (WITH MULTIKEY INDEX) ---');
var diagQuery = db.claims.find({ 'diagnosis.icd_code': 'I25.10' }).explain('executionStats');
print('Documents examined: ' + diagQuery.executionStats.totalDocsExamined);
print('Documents returned: ' + diagQuery.executionStats.nReturned);

// Benchmark 5: Text search
print('\n--- Text Search: "surgery" (WITH TEXT INDEX) ---');
var textQuery = db.claims.find({ $text: { $search: 'surgery' } }).explain('executionStats');
print('Documents examined: ' + textQuery.executionStats.totalDocsExamined);
print('Documents returned: ' + textQuery.executionStats.nReturned);

// List all indexes
print('\n===== All Indexes on claims collection =====');
db.claims.getIndexes().forEach(printjson);

print('\n✅ Indexing strategy implemented and benchmarked');
