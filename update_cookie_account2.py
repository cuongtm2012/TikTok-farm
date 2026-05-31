#!/usr/bin/env python3
import sqlite3, json

cookie_str = "tt_csrf_token=ank8CKcf-1dBXPSnAZ3qrgKJSoNsRscTR5FY; s_v_web_id=verify_mpi9b7fs_sLaTLuVq_yft9_4f4z_Aae9_rZ1DyIiPWt4f; msToken=UNcXbVDXpGRCmtNrJc-gBX_Bw9MFGFGWMO6WBQs72A7zujxRrUXV-TtbpAG1gvWXuDfr5x87DFxJ73Z0EPaWjZzQ8hr3ROuA3Y2qJgU1WJ0JA-AYylZVpPDRNgCqWX0JglNAaR5E9DLQsw==; multi_sids=7643045421153518593%3A5de842e63b44fd6c45603f26dddb8753; cmpl_token=AgQYAPOF_hfkTtK6L2P3ajrdOPBwFCe5TL-WDmCnF9w; sid_guard=5de842e63b44fd6c45603f26dddb8753%7C1779535211%7C15552000%7CThu%2C+19-Nov-2026+11%3A20%3A11+GMT; uid_tt=39605fe809cf56643c8d11c38eb96e4b62481951fdfd9cb42d18abf6d21e9416; uid_tt_ss=39605fe809cf56643c8d11c38eb96e4b62481951fdfd9cb42d18abf6d21e9416; sid_tt=5de842e63b44fd6c45603f26dddb8753; sessionid=5de842e63b44fd6c45603f26dddb8753; sessionid_ss=5de842e63b44fd6c45603f26dddb8753; tt_session_tlb_tag=sttt%7C3%7CXehC5jtE_WxFYD8m3duHU__________umMaavb9_g2LZ-RIIGHxJTeLClPNEvkIFsrcduVq0YGE%3D; sid_ucp_v1=1.0.1-KGM0MzI2NDUwODExYzAxYmU1N2M3NmM5ZjQyMGIxMDQ1NzRlZmY4NGYKIgiBiKCQjqrjiGoQ65rG0AYYswsgDDDqmsbQBjgBQOsHSAQQAxoDc2cxIiA1ZGU4NDJlNjNiNDRmZDZjNDU2MDNmMjZkZGRiODc1MzJOCiBRo2FAzl7F9vIsRY8FBOCfbhaVPRHOSxxmOjgiphcgHBIgkMOzRHbFWn9wNck_QUzmP47a_J3b91Hwqp5M6FIRxN4YASIGdGlrdG9r; ssid_ucp_v1=1.0.1-KGM0MzI2NDUwODExYzAxYmU1N2M3NmM5ZjQyMGIxMDQ1NzRlZmY4NGYKIgiBiKCQjqrjiGoQ65rG0AYYswsgDDDqmsbQBjgBQOsHSAQQAxoDc2cxIiA1ZGU4NDJlNjNiNDRmZDZjNDU2MDNmMjZkZGRiODc1MzJOCiBRo2FAzl7F9vIsRY8FBOCfbhaVPRHOSxxmOjgiphcgHBIgkMOzRHbFWn9wNck_QUzmP47a_J3b91Hwqp5M6FIRxN4YASIGdGlrdG9r; store-idc=alisg; store-country-code=vn; store-country-code-src=uid; tt-target-idc=alisg; tt-target-idc-sign=AhTp6hYU_6Iywcgzegwu4qxcpDOO5CGP-6dG17h-tC1kc5Kd6JTcvEZVbcMW7P4JQxSv3ufoqn7Fx6InBbBxYbAMZet_D5C3tJrZeWTqiszOikQ3vkKysS-xtH-K0g6-JYTO-8lhls78c-U4EbDtUPrh6axNql8mCBkBku5yL5UjVWpyWpepp4WLNr7zMFMFIph4uWmgQDvrj9OFYDfW-iw1M23cpYM4Bui9xNipyeZqPRlChYW5DzahzXDi67rvGt97L2WULZGcUwG_bxKEkBnA5wLttiQINY9YDbEtUxUhnNNIxJ5tbJou-nwPe3hPNB6-SYBD5vr5VYfovdNE7fOzVWZmofmCghxnjQUgUJP_6ku1gU3CKrKxbuTj1naC6sugr1_5UO73383TXR3NpNBqM8Hh_N8_IKiTUdXxarOAwoCL1EbtVfKapI8_8b0z0Z2NR8bPGyXwS3TqrpGVXIKtoDfD3t-p5HcNu8pqdt6ArarCHMt6UaXF4x6IoR0_; last_login_method=email; store-country-sign=MEIEDEMZCmBHZPQ8NHaCbQQgMzqW_vvn_9vvGqiQEjSWNKO8cJKZXPqXV7Cm5LwOcS0EEPEHBnLpGjIMmK7Fu3bk4XY; tt_chain_token=DC83dzlGmNpf2Lrq2lHQlg==; odin_tt=7f8ffb5a44386f69e6aa9a2e84fde6b30407e0274d7c1bc6d6db72ec88fce08f2ec7af304c75f7c5f03c85a6d5bb31df3c1a890b08be4dcf27b54c17106ed985637143f5ba61888ef354f6ebff1af5f3; ttwid=1%7CNrUpuoKMM6wqW04ymYN3QWQPkT3_bTyJO08q7Rp3Rk8%7C1779535245%7Cc104b2f3b55e78939c313bbf0a2b43a73ee8d81c04397768914ad6bddd87eb5c; msToken=HawwhKfHxvl-UQ94GCeddQnStzJQVWLsuuLF1vrDbOH_ByKdHQ7P85gwKH_IdZIJ0FW0RaWe62-7rEp_KJkcyvrbfvfdSg8jeVkC22GuZ1QQrtIzKBs0UtZGbHumURbzcx5gU9qCoxM6sQ=="

cookies = []
for pair in cookie_str.split("; "):
    if "=" in pair:
        n, v = pair.split("=", 1)
        cookies.append({"name": n.strip(), "value": v.strip(), "domain": ".tiktok.com", "path": "/"})

print(f"Parsed {len(cookies)} cookies")
conn = sqlite3.connect("/app/data/farm.db")
conn.execute("UPDATE accounts SET cookie_data = ? WHERE id = 2", (json.dumps(cookies),))
conn.commit()
conn.close()
print("Saved to account 2")
