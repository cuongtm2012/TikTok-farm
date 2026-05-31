#!/usr/bin/env python3
import sqlite3, json

cookie_str = "tt_csrf_token=Gm05uOyK-iXe37rT4GkwFJj-_EPQ4TYqHj3Y; store-country-sign=MEIEDODuTADuEGKdU88CzAQggLz5IMz7-BLfXXKBRXKRkbnjxoXa2Z8IKZRM__0wLi0EEA--GgveysOkrTJo_EV6FAg; msToken=8mLW9QGWCGpaA_TG6Rkw6iAYD9sY0vUvLOGtYIRs8eCLusG6Njlbu4vr5ZIP6GvvQTPR4zJjgVSceNwpwZvTlu7LIzJVqEMkzWKCtaMzVGAYmMUGQaPer848FAth9Xp_rCVBgdzuS1Semg==; msToken=8mLW9QGWCGpaA_TG6Rkw6iAYD9sY0vUvLOGtYIRs8eCLusG6Njlbu4vr5ZIP6GvvQTPR4zJjgVSceNwpwZvTlu7LIzJVqEMkzWKCtaMzVGAYmMUGQaPer848FAth9Xp_rCVBgdzuS1Semg==; csrf_session_id=51eb96ef2a53204781ac8e9b89f0cbbd; tt_ticket_guard_has_set_public_key=1; tt-target-idc-sign=gdxKiB7XSfqvdXUcPyck7vfV8l4mQfE-GuAoD0RbDWWJVTYyCvlHo_ioLiJsowY_EKSJ_gNyECQbAa8EL3ZOsAZ0rEk_HgmciZJpjceJ7DJ13S8L6-q8iO71MMSEZd4x1I5R3CGciCS-SC76HhHaY2Ko0YbGzr_lBSaYTwaVY0uY7OaNkbEUNhXfFaDcH7VnUhGgsp9zM3P73Rna5G16HRUzRNcQCHWraq9WMjIJeXsEeULTyCdezHXiF5Xw3SCM4s0dOl7NoXJKPpnpEWrdC2hAqL3SRBD6XAMyHvJBJklvFSoa8s1RCqyM9F6JgvcxxPPLoOladQj-IaEBv7S7_jnj3jglxbSyoAjjAWii0kl852xjPch8n-dS85D2jkr-AvEabn4qlPF3JloSAYsymm9zbDSkTU_VTmwv_NX5-00ZFJqHRRbmCpPgt4sqjtjd_W-gPJm3Dc5FcWby3i7tyP8EvUupjN2tQQ_l5dmI3Xh7ss5jnOv8YXOYO8epNC0U; ttwid=1%7Ci6aDCXvFzoRq9G8tfAYqGWWJ63wNFOxWLqj9gL3oG-M%7C1779610965%7C8c112b1aef43ff47b1700d7f61b5e2f166c3aed2a84c90ab6bf0d88adfd13cfd; store-idc=alisg; sid_tt=6bb7fa27630c1734485c22c606f57064; odin_tt=e9d5bb65329254f628e1eb452cd1a819792df825d79ee4f78bfd0bca32cb0f066c5f678c772eec2797da6b048f79570703bf35a79d1618d243f61f13649813ee3fea0414877b4d0058ccf23881cb0f2e; _waftokenid=eyJ2Ijp7ImEiOiI1dDRqclJVajVISmVUZGNQTDVCUDVHZlorTWNMVU5nMzNOQmIvRmxScTRJPSIsImIiOjE3Nzk2MTA4NjgsImMiOiJkZkhuT1crMEZBVjhzd3h3RnNyRk50bXMza3VSN0Z2c0ZEUWEwUHdpRFNrPSJ9LCJzIjoiRlU4VlFoM21PR0dFRWdNWVJUZE1GVXUvMjA0cFVhM1FXdElmZG9ocjRZaz0ifQ; uid_tt_ss=142d74079c271226d68bf6cff23515e64074d66823b622e4a69ef648aca67c06; delay_guest_mode_vid=5; uid_tt=142d74079c271226d68bf6cff23515e64074d66823b622e4a69ef648aca67c06; ssid_ucp_v1=1.0.1-KDljZjFlOTVhODBkZDg4MGJiYTBiODc4MzJjYjU0MjRmYTQ4MTdlZTkKIgiViKaQ29asiWoQ2unK0AYYswsgDDDI5crQBjgBQOoHSAQQAxoCbXkiIDZiYjdmYTI3NjMwYzE3MzQ0ODVjMjJjNjA2ZjU3MDY0Mk4KIA2uRYHu_OhZbpO-SPG7x75m_yOdFQSN70L2Fh6kXEApEiCL5HcV9j2_3UqVjsDXnyKmBFY072huTVoaqtop5gBE7BgDIgZ0aWt0b2s; sid_ucp_v1=1.0.1-KDljZjFlOTVhODBkZDg4MGJiYTBiODc4MzJjYjU0MjRmYTQ4MTdlZTkKIgiViKaQ29asiWoQ2unK0AYYswsgDDDI5crQBjgBQOoHSAQQAxoCbXkiIDZiYjdmYTI3NjMwYzE3MzQ0ODVjMjJjNjA2ZjU3MDY0Mk4KIA2uRYHu_OhZbpO-SPG7x75m_yOdFQSN70L2Fh6kXEApEiCL5HcV9j2_3UqVjsDXnyKmBFY072huTVoaqtop5gBE7BgDIgZ0aWt0b2s; store-country-code-src=uid; tt-target-idc=alisg; sid_guard=6bb7fa27630c1734485c22c606f57064%7C1779610842%7C15551999%7CFri%2C+20-Nov-2026+08%3A20%3A41+GMT; cmpl_token=AgQYAPOF_hfkTtK6LFwSOjtdLPBzK_JLkb-TP2CnDOA; tt_chain_token=xIQdKHpq1c2jPwMjpOOj8g==; sessionid=6bb7fa27630c1734485c22c606f57064; last_login_method=email; store-country-code=vn; perf_feed_cache={%22expireTimestamp%22:1780214400000%2C%22itemIds%22:[%227625405562049531143%22%2C%227640529000208551186%22%2C%227619914016814583048%22]}; multi_sids=7643368011046945813%3A6bb7fa27630c1734485c22c606f57064; tiktok_webapp_theme=light; tt_session_tlb_tag=sttt%7C1%7Ca7f6J2MMFzRIXCLGBvVwZP________-okE_k0zvgRHSEuHs-hGcDSkrN3SMVsbUjAmomEpdmN84%3D; passport_fe_beating_status=true; sessionid_ss=6bb7fa27630c1734485c22c606f57064; tiktok_webapp_theme_source=auto; x-web-secsdk-uid=8fccd1f5-42d1-4ac0-a450-42273513017d; s_v_web_id=verify_mpji14gy_Mbiko2H0_NITv_4lhc_ABJK_0p5VOKi6nku3; d_ticket=76b3bf3a5e7407eb687b79b0b80204e555f4c"

cookies = []
for pair in cookie_str.split("; "):
    if "=" in pair:
        n, v = pair.split("=", 1)
        cookies.append({"name": n.strip(), "value": v.strip(), "domain": ".tiktok.com", "path": "/"})

print(f"Parsed {len(cookies)} cookies")
conn = sqlite3.connect("/app/data/farm.db")

# Check account 1
row = conn.execute("SELECT id, username FROM accounts WHERE id = 1").fetchone()
if row:
    print(f"Updating account ID {row[0]}: @{row[1]}")
    conn.execute("UPDATE accounts SET cookie_data = ? WHERE id = 1", (json.dumps(cookies),))
else:
    # Account 1 doesn't exist, maybe it got recreated
    row2 = conn.execute("SELECT id, username FROM accounts WHERE username LIKE '%1673074451623%'").fetchone()
    if row2:
        print(f"Found account by username: ID {row2[0]}: @{row2[1]}")
        conn.execute("UPDATE accounts SET cookie_data = ? WHERE id = ?", (json.dumps(cookies), row2[0]))
    else:
        print("Account 1 not found!")

conn.commit()
conn.close()
print("Done!")
