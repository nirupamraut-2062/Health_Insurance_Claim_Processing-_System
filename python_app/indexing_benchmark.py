import time
from db_config import USE_MOCK

def run_indexing_benchmark(db):
    print("\n" + "="*50)
    print("  [INDEX] RUNNING INDEXING BENCHMARK")
    print("="*50)
    
    if USE_MOCK:
        print("Note: Running with mongomock. Indexing benefits might not be accurate.")
        
    try:
        # 1. Drop existing indexes
        print("\nDropping existing indexes on 'claims' collection...")
        db.claims.drop_indexes()
        print("Indexes dropped.")
    except Exception as e:
        print(f"Note: Could not drop indexes (mongomock limitation?): {e}")

    # 2. Query without index
    query = {'status': 'Approved', 'billed_amount': {'$gt': 50000}}
    print(f"\nQuerying without index: {query}")
    start_time = time.time()
    results_unindexed = list(db.claims.find(query))
    unindexed_time = (time.time() - start_time) * 1000
    print(f"Found {len(results_unindexed)} records in {unindexed_time:.2f} ms")

    # 3. Create indexes
    print("\nCreating indexes...")
    try:
        # Single Field Index
        db.claims.create_index('status')
        # Compound Index
        db.claims.create_index([('status', 1), ('billed_amount', -1)])
        # Multikey Index on diagnosis array
        db.claims.create_index('diagnosis.icd_code')
        print("Indexes created successfully.")
    except Exception as e:
        print(f"Note: Could not create indexes (mongomock limitation?): {e}")

    # 4. Query with index
    print(f"\nQuerying with index: {query}")
    start_time = time.time()
    results_indexed = list(db.claims.find(query))
    indexed_time = (time.time() - start_time) * 1000
    print(f"Found {len(results_indexed)} records in {indexed_time:.2f} ms")

    # 5. List indexes
    try:
        indexes = list(db.claims.list_indexes())
        print("\nCurrent Indexes on 'claims':")
        for idx in indexes:
            print(f"  - {idx['name']}: {idx['key']}")
    except Exception:
        print("Note: Could not list indexes.")

    print("\n--- BENCHMARK RESULTS ---")
    print(f"Unindexed Query Time: {unindexed_time:.2f} ms")
    print(f"Indexed Query Time:   {indexed_time:.2f} ms")
    if indexed_time < unindexed_time:
        improvement = ((unindexed_time - indexed_time) / unindexed_time) * 100
        print(f"Improvement: {improvement:.2f}%")
    else:
        print("Improvement: N/A (Data size may be too small or mock DB overhead)")
