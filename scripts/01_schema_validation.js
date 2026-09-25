// Health Insurance Claim Processing System - Schema Validation
// Run: mongosh < scripts/01_schema_validation.js

use('health_insurance_db');

// Drop existing collections
db.patients.drop();
db.insurers.drop();
db.policies.drop();
db.hospitals.drop();
db.doctors.drop();
db.claims.drop();

// Create collections with $jsonSchema validators
db.createCollection('patients', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['patient_id', 'name', 'date_of_birth', 'gender', 'contact'],
      properties: {
        patient_id: { bsonType: 'string', description: 'Unique patient identifier' },
        name: { bsonType: 'string' },
        date_of_birth: { bsonType: 'string' },
        gender: { enum: ['Male', 'Female', 'Other'] },
        contact: { bsonType: 'object' },
        address: { bsonType: 'object' },
        blood_group: { bsonType: 'string' }
      }
    }
  }
});

db.createCollection('insurers', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['insurer_id', 'name', 'license_no', 'contact', 'headquarters'],
      properties: {
        insurer_id: { bsonType: 'string' },
        name: { bsonType: 'string' },
        license_no: { bsonType: 'string' },
        contact: { bsonType: 'string' },
        support_email: { bsonType: 'string' },
        rating: { bsonType: 'number' },
        headquarters: { bsonType: 'string' }
      }
    }
  }
});

db.createCollection('policies', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['policy_id', 'patient_id', 'insurer_id', 'policy_name', 'coverage_amount', 'status'],
      properties: {
        policy_id: { bsonType: 'string' },
        patient_id: { bsonType: 'string' },
        insurer_id: { bsonType: 'string' },
        policy_name: { bsonType: 'string' },
        coverage_amount: { bsonType: 'number' },
        remaining_coverage: { bsonType: 'number' },
        premium: { bsonType: 'number' },
        validity: { bsonType: 'object' },
        dependents: { bsonType: 'array' },
        status: { enum: ['Active', 'Expired', 'Suspended'] }
      }
    }
  }
});

db.createCollection('hospitals', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['hospital_id', 'name', 'city', 'state', 'tier'],
      properties: {
        hospital_id: { bsonType: 'string' },
        name: { bsonType: 'string' },
        city: { bsonType: 'string' },
        state: { bsonType: 'string' },
        tier: { enum: ['Tier-1', 'Tier-2', 'Tier-3'] },
        beds: { bsonType: 'number' },
        empanelment: { bsonType: 'object' }
      }
    }
  }
});

db.createCollection('doctors', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['doctor_id', 'name', 'specialization', 'hospital_id', 'registration_no'],
      properties: {
        doctor_id: { bsonType: 'string' },
        name: { bsonType: 'string' },
        specialization: { bsonType: 'string' },
        hospital_id: { bsonType: 'string' },
        registration_no: { bsonType: 'string' },
        experience_years: { bsonType: 'number' }
      }
    }
  }
});

// claims collection should have the most detailed schema:
db.createCollection('claims', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['claim_id', 'policy_id', 'patient_id', 'hospital_id', 'claim_type', 'billed_amount', 'status'],
      properties: {
        claim_id: { bsonType: 'string' },
        policy_id: { bsonType: 'string' },
        patient_id: { bsonType: 'string' },
        hospital_id: { bsonType: 'string' },
        doctor_id: { bsonType: 'string' },
        billed_amount: { bsonType: 'number', minimum: 0 },
        approved_amount: { bsonType: 'number', minimum: 0 },
        deductible_amount: { bsonType: 'number', minimum: 0 },
        status: { enum: ['Submitted', 'Under Review', 'Approved', 'Rejected', 'Settled', 'Cancelled'] },
        claim_type: { enum: ['Inpatient', 'Outpatient', 'Emergency', 'Maternity', 'Surgical'] },
        claim_date: { bsonType: 'string' },
        admission_date: { bsonType: 'string' },
        discharge_date: { bsonType: 'string' },
        treatment_details: { bsonType: 'object' },
        diagnosis: { bsonType: 'array' },
        status_history: { bsonType: 'array' },
        supporting_documents: { bsonType: 'array' }
      }
    }
  }
});

print('✅ All collections created with schema validation');
