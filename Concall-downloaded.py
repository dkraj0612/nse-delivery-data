import requests
import json

url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
params = {
    "pageno": 1,
    "strCat": "-1",
    "strPrevDate": "20260520",
    "strToDate": "20260525",
    "strScrip": "",
    "strSearch": "P"
}

resp = requests.get(url, params=params, timeout=30)
data = resp.json()

# Show actual field names
if data.get('Table'):
    first_record = data['Table'][0]
    print("ACTUAL FIELDS IN API RESPONSE:")
    for key, value in first_record.items():
        print(f"  {key}: {value}")
    
    # Save full response to examine
    with open('api_response.json', 'w') as f:
        json.dump(data, f, indent=2)
    print("\nFull response saved to api_response.json")
