import urllib.request, json, sys

BASE = "https://wallet-service-production-1cf9.up.railway.app"

def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

action = sys.argv[1] if len(sys.argv) > 1 else "register"

if action == "register":
    status, data = post("/auth/register", {
        "username": "prashant",
        "email": "prashantkhandelwal001@gmail.com",
        "password": "Abc@123"
    })
    print(f"HTTP {status}:", json.dumps(data, indent=2))

elif action == "login":
    status, data = post("/auth/login", {
        "username": "prashant",
        "password": "Abc@123"
    })
    print(f"HTTP {status}:", json.dumps(data, indent=2))
