// CRUD Operations Demonstration
use('health_insurance_db');

// ========== CREATE ==========
print('\n========== CREATE OPERATIONS ==========');

// 1. Register a new patient
print('\n--- Creating new patient ---');
db.patients.insertOne({
  patient_id: 'P016',
  name: 'Vikram Mehta',
  date_of_birth: '1990-07-22',
  gender: 'Male',
  contact: { phone: '+91-9988776655', email: 'vikram.mehta@email.com' },
  address: { street: '15 Park Avenue', city: 'Pune', state: 'Maharashtra', pincode: '411001' },
  blood_group: 'A+'
});
print('✅ New patient P016 created');

// 2. Submit a new claim
print('\n--- Submitting new claim ---');
db.claims.insertOne({
  claim_id: 'CLM061',
  policy_id: 'POL001',
  patient_id: 'P001',
  hospital_id: 'H002',
  doctor_id: 'D003',
  claim_type: 'Outpatient',
  claim_date: '2026-09-25',
  treatment_details: {
    procedure_code: 'CPT-99213',
    description: 'Office visit - moderate complexity',
    cost_breakdown: [
      { item: 'Consultation', amount: 1500 },
      { item: 'Diagnostics', amount: 3500 },
      { item: 'Medications', amount: 2000 }
    ]
  },
  diagnosis: [{ icd_code: 'J06.9', description: 'Upper respiratory infection' }],
  billed_amount: 7000,
  approved_amount: 0,
  deductible_amount: 500,
  status: 'Submitted',
  status_history: [
    { status: 'Submitted', timestamp: new Date().toISOString(), notes: 'New claim submitted', updated_by: 'patient' }
  ],
  supporting_documents: [
    { type: 'Prescription', url: '/docs/CLM061_rx.pdf', upload_date: '2026-09-25' }
  ]
});
print('✅ New claim CLM061 submitted');

// ========== READ ==========
print('\n========== READ OPERATIONS ==========');

// 1. Find all claims for a specific patient
print('\n--- Claims for patient P001 ---');
db.claims.find({ patient_id: 'P001' }, { claim_id: 1, status: 1, billed_amount: 1, _id: 0 }).forEach(printjson);

// 2. Find claims by status
print('\n--- All Rejected Claims ---');
db.claims.find({ status: 'Rejected' }, { claim_id: 1, patient_id: 1, billed_amount: 1, _id: 0 }).forEach(printjson);

// 3. Find claims with billed amount > 100000
print('\n--- High-value claims (> ₹1,00,000) ---');
db.claims.find({ billed_amount: { $gt: 100000 } }, { claim_id: 1, billed_amount: 1, status: 1, _id: 0 }).sort({ billed_amount: -1 }).forEach(printjson);

// 4. Lookup: Get claim with patient and hospital details
print('\n--- Claim CLM001 with Patient & Hospital details (using $lookup) ---');
db.claims.aggregate([
  { $match: { claim_id: 'CLM001' } },
  { $lookup: { from: 'patients', localField: 'patient_id', foreignField: 'patient_id', as: 'patient_info' } },
  { $lookup: { from: 'hospitals', localField: 'hospital_id', foreignField: 'hospital_id', as: 'hospital_info' } },
  { $project: { claim_id: 1, status: 1, billed_amount: 1, 'patient_info.name': 1, 'hospital_info.name': 1, _id: 0 } }
]).forEach(printjson);

// ========== UPDATE ==========
print('\n========== UPDATE OPERATIONS ==========');

// 1. Update claim status with audit trail
print('\n--- Moving CLM061 from Submitted to Under Review ---');
db.claims.updateOne(
  { claim_id: 'CLM061' },
  {
    $set: { status: 'Under Review' },
    $push: {
      status_history: {
        status: 'Under Review',
        timestamp: new Date().toISOString(),
        notes: 'Assigned to claims adjuster',
        updated_by: 'system'
      }
    }
  }
);
print('✅ CLM061 status updated to Under Review');
printjson(db.claims.findOne({ claim_id: 'CLM061' }, { status: 1, status_history: 1, _id: 0 }));

// 2. Update approved amount and approve claim
print('\n--- Approving CLM061 ---');
db.claims.updateOne(
  { claim_id: 'CLM061' },
  {
    $set: { status: 'Approved', approved_amount: 6500 },
    $push: {
      status_history: {
        status: 'Approved',
        timestamp: new Date().toISOString(),
        notes: 'Claim verified and approved',
        updated_by: 'adjuster_102'
      }
    }
  }
);
print('✅ CLM061 approved with amount ₹6,500');

// ========== DELETE ==========
print('\n========== DELETE OPERATIONS ==========');

// 1. Soft-delete: Cancel the newly created claim
print('\n--- Soft-deleting (cancelling) CLM061 ---');
db.claims.updateOne(
  { claim_id: 'CLM061' },
  {
    $set: { status: 'Cancelled', cancelled_at: new Date().toISOString() },
    $push: {
      status_history: {
        status: 'Cancelled',
        timestamp: new Date().toISOString(),
        notes: 'Claim cancelled by demonstration',
        updated_by: 'admin'
      }
    }
  }
);
print('✅ CLM061 soft-deleted (cancelled)');

// 2. Hard-delete: Remove the test patient and claim
print('\n--- Hard-deleting test data ---');
db.claims.deleteOne({ claim_id: 'CLM061' });
db.patients.deleteOne({ patient_id: 'P016' });
print('✅ Test patient P016 and claim CLM061 removed');

print('\n✅ All CRUD operations demonstrated successfully');
