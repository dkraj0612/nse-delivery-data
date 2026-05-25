import requests
import json

url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
params = {
    "pageno": 1,
    "strCat": "-1",
    "strPrevDate": "20260101",  # Start of year
    "strToDate": "20260525",    # Today
    "strScrip": "",
    "strSearch": "P"
}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

resp = requests.get(url, params=params, headers=headers, timeout=30)
print(f"HTTP Status: {resp.status_code}")
print(f"\nFull Response:\n{json.dumps(resp.json(), indent=2)[:2000]}")  # First 2000 chars
