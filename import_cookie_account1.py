#!/usr/bin/env python3
import sqlite3, json, shlex, sys

raw = sys.argv[1]
parts = raw.split('|')
if len(parts) < 6:
    print(f"ERROR: expected 6 parts, got {len(parts)}")
    sys.exit(1)

username = parts[0]
password = parts[1]
email = parts[2]
email_pass = parts[3]
uid = parts[4]
cookie_str = '|'.join(parts[5:])

# Parse cookie string
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

print(f"Parsed {len(cookies)} cookies for @{username}")

# Save to DB - account with tiktok_uid=9e5f94bc... is account 2
conn = sqlite3.connect('/app/data/farm.db')
c = conn.execute('SELECT id, username FROM accounts WHERE username = ?', (username,)).fetchone()
if not c:
    # Find by UID or take account 2
    c = conn.execute('SELECT id, username FROM accounts WHERE id = 2').fetchone()
    if c:
        print(f"Found account ID {c[0]}: @{c[1]}")
        conn.execute('UPDATE accounts SET cookie_data = ?, tiktok_password = ?, email = ?, email_password = ?, tiktok_uid = ? WHERE id = ?',
            (json.dumps(cookies), password, email, email_pass, uid, c[0]))
    else:
        print("No account found!")
        sys.exit(1)
else:
    print(f"Found account ID {c[0]}: @{c[1]}")
    conn.execute('UPDATE accounts SET cookie_data = ?, tiktok_password = ?, email = ?, email_password = ?, tiktok_uid = ? WHERE id = ?',
        (json.dumps(cookies), password, email, email_pass, uid, c[0]))

conn.commit()
conn.close()
print("Done!")
