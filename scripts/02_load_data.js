// ClaimSure - 02. Load the data set (MongoDB Extended JSON) and initialise sequence counters
//   mongosh "mongodb://localhost:27017" --file scripts/02_load_data.js

const fs = require('fs');
db = db.getSiblingDB('claimsure');

const collections = ['insurers', 'tpas', 'plans', 'icd_codes', 'hospitals', 'doctors', 'users', 'agents',
                     'members', 'policies', 'claims', 'payments', 'grievances', 'audit_logs'];
let total = 0;
for (const name of collections) {
  // EJSON.parse turns {"$date": "..."} into real BSON dates
  const docs = EJSON.parse(fs.readFileSync(`data/${name}.json`, 'utf8'), { relaxed: true });
  db.getCollection(name).deleteMany({});
  db.getCollection(name).insertMany(docs, { ordered: false });
  total += docs.length;
  print(`  ${name.padEnd(12)} ${String(docs.length).padStart(6)} documents`);
}

// Counters hold the last number issued for each business id (see claimsure/services.next_id)
db.counters.deleteMany({});
for (const [key, coll, field] of [['claim', 'claims', 'claim_number'], ['payment', 'payments', 'payment_id'],
                                  ['grievance', 'grievances', 'grievance_id'], ['member', 'members', 'member_id'],
                                  ['policy', 'policies', 'policy_number']]) {
  const top = db.getCollection(coll).find({}, { [field]: 1 }).toArray()
    .reduce((max, d) => Math.max(max, parseInt(d[field].match(/(\d+)$/)[1], 10)), 0);
  db.counters.insertOne({ _id: key, seq: top });
}

print(`\nLoaded ${total.toLocaleString()} documents into ${collections.length} collections.`);
printjson(db.counters.find().toArray());
