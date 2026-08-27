import sqlite3
import json

def get_appliance_map():
    conn = sqlite3.connect('lens.db')
    cur = conn.cursor()
    cur.execute("SELECT choice_value, label_en FROM choices WHERE variable_code IN ('mq2a', 'mq3b')")
    mapping = {}
    for val, label in cur.fetchall():
        mapping[str(val)] = label
    conn.close()
    return mapping

if __name__ == "__main__":
    print(json.dumps(get_appliance_map(), indent=2))
