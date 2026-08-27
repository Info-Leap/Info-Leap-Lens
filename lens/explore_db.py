import sqlite3
import pandas as pd

conn = sqlite3.connect('lens.db')
query = "SELECT variable_code, COUNT(value_text) as count_text, COUNT(value_numeric) as count_numeric FROM responses WHERE variable_code = 'mq2a' GROUP BY variable_code"
df = pd.read_sql_query(query, conn)
print(df.to_string())
conn.close()
