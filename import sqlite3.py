import sqlite3
import pandas as pd

conn = sqlite3.connect('history.db')

# 1. Print table names
tables = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table';", conn)
print("--- TABLES ---")
print(tables)

# 2. Inspect log table structure and sample data (replace 'log_table' with your table name)
table_name = tables['name'].iloc[0] 
df = pd.read_sql(f"SELECT * FROM {table_name} LIMIT 5;", conn)

print(f"\n--- COLUMNS IN {table_name} ---")
print(df.dtypes)

print("\n--- SAMPLE ROWS ---")
print(df.head())

# 3. Print summary stats for numerical columns
print("\n--- SUMMARY STATS ---")
print(pd.read_sql(f"SELECT * FROM {table_name}", conn).describe(include='all').T[['count', 'mean', 'std', 'min', '50%', 'max']])

conn.close()