#!/usr/bin/env python3
import sqlite3, json, sys

raw = sys.argv[1]
parts = raw.split('|')
cookie_str = '|'.join(parts[5:])

# Parse cookie
cookies = []
for pair in cookie_str.split('; '):
    if '=' in pair:
        name, value = pair.split('=', 1)
        cookies.append({
            'name': name.strip(),
            'value': value.strip(),
            'domain': '.tiktok.com',
            'path': '/'
        })

print(f"Parsed {len(cookies)} cookies")

conn = sqlite3.connect('/app/data/farm.db')
conn.execute("UPDATE accounts SET cookie_data = ? WHERE id = 2", (json.dumps(cookies),))
conn.commit()
conn.close()
print("Saved to account 2")
