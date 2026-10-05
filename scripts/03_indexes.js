// ClaimSure - 03. Build indexes (after the bulk load - faster than maintaining them during inserts)
//   mongosh "mongodb://localhost:27017" --file scripts/03_indexes.js
// Definitions come from schema/indexes.json: unique, compound, multikey, text, partial, sparse, TTL, 2dsphere.

const fs = require('fs');
db = db.getSiblingDB('claimsure');

const specs = JSON.parse(fs.readFileSync('schema/indexes.json', 'utf8'));
let count = 0;
for (const [coll, list] of Object.entries(specs)) {
  for (const spec of list) {
    const keys = {};
    spec.keys.forEach(([field, dir]) => { keys[field] = dir; });
    db.getCollection(coll).createIndex(keys, spec.options);
    count += 1;
    print(`  ${coll.padEnd(11)} ${spec.kind.padEnd(13)} ${spec.options.name.padEnd(20)} ${spec.purpose}`);
  }
}
print(`\n${count} indexes created.`);

// Prove the unique index on payments.claim_number prevents paying a claim twice
const anyPayment = db.payments.findOne({}, { _id: 0 });
try {
  db.payments.insertOne(anyPayment);
} catch (e) {
  print(`\nDuplicate payment rejected as expected: E${e.code} ${e.codeName || ''}`);
}
