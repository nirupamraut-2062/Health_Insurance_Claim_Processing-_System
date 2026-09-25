const fs = require('fs');
const path = require('path');

const dataDir = path.join(__dirname, 'data');
if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
}

// Data generators
const generatePatients = () => {
    const patients = [];
    const names = ["Rajesh Kumar", "Priya Sharma", "Amit Patel", "Sneha Gupta", "Vikram Singh", "Anjali Desai", "Rahul Verma", "Kavita Reddy", "Sanjay Joshi", "Neha Malhotra", "Arun Nambiar", "Pooja Banerjee", "Deepak Chawla", "Ritu Agarwal", "Manoj Tiwari"];
    const cities = ["Mumbai", "Delhi", "Bangalore", "Chennai", "Hyderabad", "Pune", "Kolkata", "Jaipur", "Lucknow", "Ahmedabad", "Chandigarh", "Indore", "Bhopal", "Surat", "Patna"];
    for (let i = 1; i <= 15; i++) {
        patients.push({
            patient_id: `P${i.toString().padStart(3, '0')}`,
            name: names[i - 1],
            date_of_birth: `19${60 + (i % 30)}-${(i % 12) + 1}-${(i % 28) + 1}`,
            gender: i % 2 === 0 ? "Female" : "Male",
            contact: { phone: `+91-98765${i.toString().padStart(5, '0')}`, email: `${names[i-1].split(' ')[0].toLowerCase()}@email.com` },
            address: { street: `${i} Main Road`, city: cities[i - 1], state: "State", pincode: `4000${i.toString().padStart(2, '0')}` },
            blood_group: ["A+", "B+", "O+", "AB+", "A-", "B-", "O-", "AB-"][i % 8]
        });
    }
    return patients;
};

const generateInsurers = () => {
    const names = ["Star Health Insurance", "ICICI Lombard", "Max Bupa", "HDFC Ergo", "New India Assurance"];
    const insurers = [];
    for (let i = 1; i <= 5; i++) {
        insurers.push({
            insurer_id: `INS${i.toString().padStart(3, '0')}`,
            name: names[i - 1],
            license_no: `IRDAI-HI-200${i}`,
            contact: `+91-1800-123-456${i}`,
            support_email: `support@${names[i-1].replace(/\s+/g, '').toLowerCase()}.in`,
            rating: 4 + (i % 5) * 0.1,
            headquarters: ["Chennai", "Mumbai", "Delhi", "Mumbai", "Mumbai"][i - 1]
        });
    }
    return insurers;
};

const generatePolicies = () => {
    const policies = [];
    const statuses = ["Active", "Active", "Active", "Active", "Active", "Active", "Active", "Active", "Active", "Active", "Active", "Active", "Expired", "Expired", "Suspended"];
    for (let i = 1; i <= 15; i++) {
        policies.push({
            policy_id: `POL${i.toString().padStart(3, '0')}`,
            patient_id: `P${i.toString().padStart(3, '0')}`,
            insurer_id: `INS${((i % 5) + 1).toString().padStart(3, '0')}`,
            policy_name: ["Family Health Optima", "Health Suraksha", "Optima Restore", "My:Health Medisure", "Arogya Sanjeevani"][i % 5],
            coverage_amount: 500000 + (i * 100000),
            remaining_coverage: 400000 + (i * 50000),
            premium: 10000 + (i * 1000),
            validity: { start_date: "2025-01-01", end_date: "2026-12-31" },
            dependents: ["Dependent 1", "Dependent 2"],
            status: statuses[i - 1]
        });
    }
    return policies;
};

const generateHospitals = () => {
    const hospitals = [];
    const names = ["Apollo Hospitals", "Fortis Hospital", "AIIMS", "Max Hospital", "Manipal Hospital", "Medanta"];
    const cities = ["Chennai", "Delhi", "Delhi", "Gurgaon", "Bangalore", "Gurgaon"];
    const tiers = ["Tier-1", "Tier-1", "Tier-1", "Tier-1", "Tier-2", "Tier-1"];
    for (let i = 1; i <= 6; i++) {
        hospitals.push({
            hospital_id: `H${i.toString().padStart(3, '0')}`,
            name: names[i - 1],
            city: cities[i - 1],
            state: "State",
            tier: tiers[i - 1],
            beds: 300 + (i * 50),
            empanelment: {
                "INS001": true, "INS002": true, "INS003": i % 2 === 0, "INS004": true, "INS005": true
            }
        });
    }
    return hospitals;
};

const generateDoctors = () => {
    const doctors = [];
    const specs = ["Cardiology", "Orthopedics", "General Surgery", "Oncology", "Neurology", "Gynecology", "Pulmonology", "Nephrology", "Gastroenterology", "Emergency Medicine"];
    for (let i = 1; i <= 10; i++) {
        doctors.push({
            doctor_id: `D${i.toString().padStart(3, '0')}`,
            name: `Dr. Doctor ${i}`,
            specialization: specs[i - 1],
            hospital_id: `H${((i % 6) + 1).toString().padStart(3, '0')}`,
            registration_no: `MCI-2015-${10000 + i}`,
            experience_years: 5 + i
        });
    }
    return doctors;
};

const generateClaims = () => {
    const claims = [];
    const claimTypes = ["Inpatient", "Outpatient", "Emergency", "Maternity", "Surgical"];
    const typeDistribution = [];
    for (let i=0; i<20; i++) typeDistribution.push("Inpatient");
    for (let i=0; i<15; i++) typeDistribution.push("Outpatient");
    for (let i=0; i<10; i++) typeDistribution.push("Emergency");
    for (let i=0; i<5; i++) typeDistribution.push("Maternity");
    for (let i=0; i<10; i++) typeDistribution.push("Surgical");

    const statusDistribution = [];
    for (let i=0; i<15; i++) statusDistribution.push("Submitted");
    for (let i=0; i<12; i++) statusDistribution.push("Under Review");
    for (let i=0; i<15; i++) statusDistribution.push("Approved");
    for (let i=0; i<8; i++) statusDistribution.push("Rejected");
    for (let i=0; i<10; i++) statusDistribution.push("Settled");

    const icdCodes = ["I25.10", "S82.0", "O80", "E11.9", "J18.9", "K80.20", "M17.1", "C50.9", "G20", "N18.3"];
    
    for (let i = 1; i <= 60; i++) {
        const patientIdx = (i % 15) + 1;
        const hospitalIdx = (i % 6) + 1;
        const doctorIdx = (i % 10) + 1;
        
        const billed = 5000 + (Math.random() * 495000);
        let approved = 0;
        const status = statusDistribution[i - 1];
        if (status === "Approved" || status === "Settled") {
            approved = billed * 0.9; // 90% approved
        }

        claims.push({
            claim_id: `CLM${i.toString().padStart(3, '0')}`,
            policy_id: `POL${patientIdx.toString().padStart(3, '0')}`,
            patient_id: `P${patientIdx.toString().padStart(3, '0')}`,
            hospital_id: `H${hospitalIdx.toString().padStart(3, '0')}`,
            doctor_id: `D${doctorIdx.toString().padStart(3, '0')}`,
            claim_type: typeDistribution[i - 1],
            claim_date: `2026-${String((i % 12) + 1).padStart(2, '0')}-15`,
            admission_date: `2026-${String((i % 12) + 1).padStart(2, '0')}-10`,
            discharge_date: `2026-${String((i % 12) + 1).padStart(2, '0')}-14`,
            treatment_details: {
                procedure_code: `CPT-${30000 + i}`,
                description: `Medical procedure ${i}`,
                cost_breakdown: [
                    { item: "Room Charges", amount: billed * 0.2 },
                    { item: "Medications", amount: billed * 0.3 },
                    { item: "Diagnostics", amount: billed * 0.5 }
                ]
            },
            diagnosis: [
                { icd_code: icdCodes[i % 10], description: "Medical condition" }
            ],
            billed_amount: Math.round(billed),
            approved_amount: Math.round(approved),
            deductible_amount: 5000,
            status: status,
            status_history: [
                { status: "Submitted", timestamp: "2026-01-15T09:00:00Z", notes: "Claim submitted", updated_by: "patient" },
                ...(status === "Rejected" ? [{ status: "Rejected", timestamp: "2026-01-18T14:00:00Z", notes: "Not covered under policy terms", updated_by: "system" }] : [])
            ],
            supporting_documents: [
                { type: "Discharge Summary", url: `/docs/CLM${i.toString().padStart(3, '0')}_discharge.pdf`, upload_date: "2026-01-15" }
            ]
        });
    }
    return claims;
};

fs.writeFileSync(path.join(dataDir, 'patients.json'), JSON.stringify(generatePatients(), null, 2));
fs.writeFileSync(path.join(dataDir, 'insurers.json'), JSON.stringify(generateInsurers(), null, 2));
fs.writeFileSync(path.join(dataDir, 'policies.json'), JSON.stringify(generatePolicies(), null, 2));
fs.writeFileSync(path.join(dataDir, 'hospitals.json'), JSON.stringify(generateHospitals(), null, 2));
fs.writeFileSync(path.join(dataDir, 'doctors.json'), JSON.stringify(generateDoctors(), null, 2));
fs.writeFileSync(path.join(dataDir, 'claims.json'), JSON.stringify(generateClaims(), null, 2));

console.log('All files generated successfully.');
