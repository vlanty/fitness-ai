#!/usr/bin/env python3
import sys
import webbrowser
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

CLIENT_ID = input("Введи Strava Client ID: ").strip()
CLIENT_SECRET = input("Введи Strava Client Secret: ").strip()

auth_code = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = parse_qs(urlparse(self.path).query)
        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>OK! Can close this tab.</h1>")
    def log_message(self, *args):
        pass

auth_url = "https://www.strava.com/oauth/authorize?client_id=" + CLIENT_ID + "&redirect_uri=http://localhost:8888&response_type=code&scope=activity:read_all"

print("\nОткрываю браузер...")
webbrowser.open(auth_url)

server = HTTPServer(("localhost", 8888), Handler)
server.handle_request()

if not auth_code:
    print("Ошибка: код не получен")
    sys.exit(1)

resp = requests.post("https://www.strava.com/oauth/token", data={
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": auth_code,
    "grant_type": "authorization_code",
})
data = resp.json()

if "refresh_token" not in data:
    print("Ошибка: " + str(data))
    sys.exit(1)

print("\nRefresh Token: " + data["refresh_token"])
print("Скопируй в terraform.tfvars -> strava_refresh_token")
