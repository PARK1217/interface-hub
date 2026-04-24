import json, urllib.request, urllib.error
target_id = 14
urllib.request.urlopen(urllib.request.Request(f'http://localhost:8000/api/interfaces/{target_id}', method='DELETE'))
try:
    urllib.request.urlopen(urllib.request.Request(f'http://localhost:8000/api/interfaces/{target_id}/execute', method='POST'))
    print('FAIL — execute should have been blocked')
except urllib.error.HTTPError as e:
    print(f'OK — execute blocked with HTTP {e.code}: {json.loads(e.read())["detail"]}')
urllib.request.urlopen(urllib.request.Request(f'http://localhost:8000/api/interfaces/{target_id}/restore', method='POST'))
print('restored for clean state')