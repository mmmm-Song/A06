"""Microsoft Graph delegated authorization for personal Microsoft accounts."""
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://login.microsoftonline.com/consumers/oauth2/v2.0/"
SCOPE = "offline_access User.Read Mail.Send"


def form(endpoint, values):
    request = urllib.request.Request(BASE + endpoint, data=urllib.parse.urlencode(values).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            error = json.load(exc)
        except ValueError:
            raise RuntimeError("Microsoft authentication failed: HTTP {}".format(exc.code)) from exc
        return error


def store(config, token):
    # Keychain owns the token; no credential is written to the project folder.
    subprocess.run(["security", "add-generic-password", "-U", "-a", config["microsoft_client_id"],
                    "-s", config["keychain_service"], "-w", token], check=True, stdout=subprocess.DEVNULL)


def authorize(config):
    client = config["microsoft_client_id"]
    result = form("devicecode", {"client_id": client, "scope": SCOPE})
    if "device_code" not in result:
        raise RuntimeError(result.get("error_description", str(result)))
    print(result["message"], flush=True)
    deadline = time.monotonic() + result["expires_in"]
    interval = result.get("interval", 5)
    while time.monotonic() < deadline:
        time.sleep(interval)
        token = form("token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                               "client_id": client, "device_code": result["device_code"]})
        if "refresh_token" in token:
            store(config, token["refresh_token"])
            print("Microsoft account connected. Refresh token saved in macOS Keychain.")
            return
        if token.get("error") == "slow_down":
            interval += 5
        elif token.get("error") != "authorization_pending":
            raise RuntimeError(token.get("error_description", str(token)))
    raise RuntimeError("Sign-in code expired; run authorization again.")


def access_token(config):
    refresh = subprocess.check_output(["security", "find-generic-password", "-s", config["keychain_service"],
                                       "-a", config["microsoft_client_id"], "-w"], text=True).strip()
    result = form("token", {"client_id": config["microsoft_client_id"], "grant_type": "refresh_token",
                            "refresh_token": refresh, "scope": SCOPE})
    if "access_token" not in result:
        raise RuntimeError("Microsoft sign-in needs attention: " + result.get("error_description", str(result)))
    if result.get("refresh_token"):
        store(config, result["refresh_token"])
    return result["access_token"]


def send(config, subject, body):
    payload = {"message": {"subject": subject, "body": {"contentType": "Text", "content": body},
                           "toRecipients": [{"emailAddress": {"address": config["recipient"]}}]},
               "saveToSentItems": True}
    request = urllib.request.Request("https://graph.microsoft.com/v1.0/me/sendMail",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + access_token(config), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 202:
                raise RuntimeError("Unexpected mail response: {}".format(response.status))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Microsoft Graph rejected the email (HTTP {}).".format(exc.code)) from exc
