// Seed Data Loader
// Run: mongosh < scripts/02_seed_data.js

use('health_insurance_db');

// Load data from JSON files using mongoimport-style insertMany
const fs = require('fs');
const patients = JSON.parse(fs.readFileSync('data/patients.json', 'utf8'));
const insurers = JSON.parse(fs.readFileSync('data/insurers.json', 'utf8'));
const policies = JSON.parse(fs.readFileSync('data/policies.json', 'utf8'));
const hospitals = JSON.parse(fs.readFileSync('data/hospitals.json', 'utf8'));
const doctors = JSON.parse(fs.readFileSync('data/doctors.json', 'utf8'));
const claims = JSON.parse(fs.readFileSync('data/claims.json', 'utf8'));

db.patients.insertMany(patients);
db.insurers.insertMany(insurers);
db.policies.insertMany(policies);
db.hospitals.insertMany(hospitals);
db.doctors.insertMany(doctors);
db.claims.insertMany(claims);

print('--- Data Load Summary ---');
print('Patients: ' + db.patients.countDocuments());
print('Insurers: ' + db.insurers.countDocuments());
print('Policies: ' + db.policies.countDocuments());
print('Hospitals: ' + db.hospitals.countDocuments());
print('Doctors: ' + db.doctors.countDocuments());
print('Claims: ' + db.claims.countDocuments());
print('Total Records: ' + (db.patients.countDocuments() + db.insurers.countDocuments() + db.policies.countDocuments() + db.hospitals.countDocuments() + db.doctors.countDocuments() + db.claims.countDocuments()));
print('✅ All sample data loaded successfully');
