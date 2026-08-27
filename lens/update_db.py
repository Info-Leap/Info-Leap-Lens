import sqlite3

def update_db():
    conn = sqlite3.connect('lens.db')
    cursor = conn.cursor()
    
    # Update single-selections stored in value_numeric for multi-select questions
    multi_vars = ('bq1b', 'bq1c', 'bq1d', 'bq1e', 'bq1f', 'mq2a', 'mq3a', 'mq3b')
    
    query = f"""
        UPDATE responses 
        SET value_text = CAST(CAST(value_numeric AS INTEGER) AS TEXT), 
            value_numeric = NULL 
        WHERE variable_code IN ({','.join(['?'] * len(multi_vars))})
          AND value_numeric IS NOT NULL 
          AND value_text IS NULL
    """
    
    cursor.execute(query, multi_vars)
    rowcount = cursor.rowcount
    conn.commit()
    conn.close()
    
    print(f"Successfully migrated {rowcount} rows in lens.db")

if __name__ == '__main__':
    update_db()
