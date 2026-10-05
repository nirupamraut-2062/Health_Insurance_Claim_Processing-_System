// ClaimSure - 01. Create collections with $jsonSchema validators
// Run from the project root:
//   mongosh "mongodb://localhost:27017" --file scripts/01_create_collections.js
// The validators are read from schema/validators.json - the same file the Python app uses.

const fs = require('fs');
db = db.getSiblingDB('claimsure');

const validators = JSON.parse(fs.readFileSync('schema/validators.json', 'utf8'));
const collections = Object.keys(validators).concat(['counters']);

collections.forEach(name => db.getCollection(name).drop());

for (const [name, schema] of Object.entries(validators)) {
  db.createCollection(name, {
    validator: { $jsonSchema: schema },
    validationLevel: 'strict',      // validate every insert and update
    validationAction: 'error',      // reject invalid documents (vs. 'warn')
  });
  print(`  created ${name.padEnd(12)} required: ${schema.required.length} fields`);
}
db.createCollection('counters');   // sequence generator, no validator needed

print(`\n${Object.keys(validators).length} collections created with $jsonSchema validators.`);
printjson(db.getCollectionInfos({ name: 'claims' })[0].options.validator.$jsonSchema.required);
