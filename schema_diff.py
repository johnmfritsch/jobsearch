#!/usr/bin/env python3
"""
JobSearch Schema Comparison Tool
Compares dev and prod database schemas to identify differences
"""
import sqlite3
import sys

def get_schema(db_path):
    try:
        db = sqlite3.connect(db_path)
        cursor = db.cursor()
        schema = {}
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
            AND name NOT LIKE 'sqlite_%'
            AND name != 'schema_migrations'
            ORDER BY name
        """)
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            cursor.execute(f'PRAGMA table_info({table})')
            columns = {row[1]: row[2] for row in cursor.fetchall()}
            schema[table] = columns
        db.close()
        return schema
    except Exception as e:
        print(f"  Error reading {db_path}: {e}")
        return None

def compare_schemas(dev_schema, prod_schema):
    differences = []
    for table, dev_cols in dev_schema.items():
        if table not in prod_schema:
            differences.append(('table', 'added', table, None, None))
            continue
        prod_cols = prod_schema[table]
        for col, col_type in dev_cols.items():
            if col not in prod_cols:
                differences.append(('column', 'added', table, col, col_type))
            elif prod_cols[col] != col_type:
                differences.append(('column', 'modified', table, col, f'{prod_cols[col]} -> {col_type}'))
        for col in prod_cols:
            if col not in dev_cols:
                differences.append(('column', 'removed', table, col, None))
    for table in prod_schema:
        if table not in dev_schema:
            differences.append(('table', 'removed', table, None, None))
    return differences

def main():
    DEV_DB  = '/volume1/Web/JobSearch_dev/data/jobsearch_dev.db'
    PROD_DB = '/volume1/Web/JobSearch/data/jobsearch.db'

    print("=== Schema Comparison: Dev vs Prod ===")

    dev_schema = get_schema(DEV_DB)
    if dev_schema is None:
        return 1

    import os
    if not os.path.exists(PROD_DB):
        print("  Prod database does not exist yet — will be created on first deploy.")
        return 0

    prod_schema = get_schema(PROD_DB)
    if prod_schema is None:
        return 1

    differences = compare_schemas(dev_schema, prod_schema)

    if not differences:
        print("  Schemas are identical")
        return 0

    print(f"\n  Found {len(differences)} difference(s):\n")
    for diff in differences:
        if diff[0] == 'table':
            symbol = '+' if diff[1] == 'added' else '-'
            print(f"  {symbol} Table: {diff[2]}")
        elif diff[0] == 'column':
            symbol = '+' if diff[1] == 'added' else '~' if diff[1] == 'modified' else '-'
            detail = f" ({diff[4]})" if diff[4] else ""
            print(f"  {symbol} {diff[2]}.{diff[3]}{detail}")

    print(f"\n  WARNING: Production schema is behind development.")
    print(f"  Create migrations for these changes before deploying.")
    return 1

if __name__ == '__main__':
    sys.exit(main())
