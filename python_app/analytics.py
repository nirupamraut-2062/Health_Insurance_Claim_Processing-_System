try:
    from tabulate import tabulate
except ImportError:
    def tabulate(data, headers, tablefmt):
        for row in data:
            print(row)
        return ""

def claim_status_summary(db):
    pipeline = [
        {'$group': {
            '_id': '$status',
            'count': {'$sum': 1},
            'total_billed': {'$sum': '$billed_amount'},
            'avg_billed': {'$avg': '$billed_amount'},
            'total_approved': {'$sum': '$approved_amount'}
        }},
        {'$sort': {'count': -1}}
    ]
    try:
        results = list(db.claims.aggregate(pipeline))
        print("\n--- Claim Status Summary ---")
        headers = ["Status", "Count", "Total Billed", "Avg Billed", "Total Approved"]
        table = [[r['_id'], r['count'], f"${r.get('total_billed', 0):.2f}", f"${r.get('avg_billed', 0):.2f}", f"${r.get('total_approved', 0):.2f}"] for r in results]
        print(tabulate(table, headers=headers, tablefmt="grid"))
    except Exception as e:
        print("Aggregation failed:", e)
        print("Pipeline:", pipeline)

def hospital_performance(db):
    pipeline = [
        {'$group': {
            '_id': '$hospital_id',
            'total_claims': {'$sum': 1},
            'total_billed': {'$sum': '$billed_amount'},
            'total_approved': {'$sum': '$approved_amount'},
            'avg_claim_value': {'$avg': '$billed_amount'}
        }},
        {'$sort': {'total_claims': -1}},
        {'$limit': 5}
    ]
    try:
        results = list(db.claims.aggregate(pipeline))
        print("\n--- Hospital Performance (Top 5) ---")
        headers = ["Hospital ID", "Total Claims", "Total Billed", "Total Approved", "Avg Claim Value"]
        table = [[r['_id'], r['total_claims'], f"${r.get('total_billed',0):.2f}", f"${r.get('total_approved',0):.2f}", f"${r.get('avg_claim_value',0):.2f}"] for r in results]
        print(tabulate(table, headers=headers, tablefmt="grid"))
    except Exception as e:
        print("Aggregation failed:", e)

def insurer_rejection_rate(db):
    pipeline = [
        {'$group': {
            '_id': '$policy_id',
            'total_claims': {'$sum': 1},
            'rejected_claims': {
                '$sum': {'$cond': [{'$eq': ['$status', 'Rejected']}, 1, 0]}
            }
        }},
        {'$project': {
            'total_claims': 1,
            'rejected_claims': 1,
            'rejection_rate': {'$multiply': [{'$divide': ['$rejected_claims', '$total_claims']}, 100]}
        }},
        {'$sort': {'rejection_rate': -1}},
        {'$limit': 5}
    ]
    try:
        results = list(db.claims.aggregate(pipeline))
        print("\n--- Insurer/Policy Rejection Rates ---")
        headers = ["Policy ID", "Total Claims", "Rejected Claims", "Rejection Rate (%)"]
        table = [[r['_id'], r['total_claims'], r['rejected_claims'], f"{r.get('rejection_rate', 0):.2f}%"] for r in results]
        print(tabulate(table, headers=headers, tablefmt="grid"))
    except Exception as e:
        print("Aggregation failed:", e)

def top_diagnoses(db):
    pipeline = [
        {'$unwind': '$diagnosis_codes'},
        {'$group': {
            '_id': '$diagnosis_codes',
            'claim_count': {'$sum': 1},
            'total_cost': {'$sum': '$billed_amount'},
            'avg_cost': {'$avg': '$billed_amount'}
        }},
        {'$sort': {'claim_count': -1}},
        {'$limit': 5}
    ]
    try:
        results = list(db.claims.aggregate(pipeline))
        print("\n--- Top Diagnoses ---")
        headers = ["ICD Code", "Claim Count", "Total Cost", "Avg Cost"]
        table = [[r['_id'], r['claim_count'], f"${r.get('total_cost',0):.2f}", f"${r.get('avg_cost',0):.2f}"] for r in results]
        print(tabulate(table, headers=headers, tablefmt="grid"))
    except Exception as e:
        print("Aggregation failed:", e)

def monthly_trends(db):
    pipeline = [
        {'$project': {
            'year_month': {'$substr': ['$claim_date', 0, 7]},
            'billed_amount': 1
        }},
        {'$group': {
            '_id': '$year_month',
            'claim_count': {'$sum': 1},
            'total_billed': {'$sum': '$billed_amount'}
        }},
        {'$sort': {'_id': 1}}
    ]
    try:
        results = list(db.claims.aggregate(pipeline))
        print("\n--- Monthly Trends ---")
        headers = ["Month", "Claim Count", "Total Billed"]
        table = [[r['_id'], r['claim_count'], f"${r.get('total_billed',0):.2f}"] for r in results]
        print(tabulate(table, headers=headers, tablefmt="grid"))
    except Exception as e:
        print("Aggregation failed:", e)

def run_all_analytics(db):
    print("\n" + "="*50)
    print("  📈 RUNNING ANALYTICS DASHBOARD")
    print("="*50)
    claim_status_summary(db)
    hospital_performance(db)
    insurer_rejection_rate(db)
    top_diagnoses(db)
    monthly_trends(db)
