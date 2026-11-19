"""Read-only classroom preflight for a running local dashboard."""
import argparse
import json
import urllib.error
import urllib.request


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    try:
        status = get_json(base + "/api/agent/status")
        current = post_json(
            base + "/api/agent/chat",
            {"message": "What is the current room status?", "history": []},
        )
        history = post_json(
            base + "/api/agent/chat",
            {
                "message": "How many people were detected in the last hour?",
                "history": [],
            },
        )
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(f"FAIL: dashboard preflight could not complete: {exc}")
        return 1

    print(f"Planner mode: {status.get('mode')}")
    if status.get("model"):
        print(f"Model: {status['model']}")
    print(f"Current-status tool: {current.get('tool')}")
    print(f"Last-hour tool: {history.get('tool')}")
    if current.get("tool") != "current_status":
        print("FAIL: current-status prompt did not route correctly")
        return 1
    if history.get("tool") != "occupancy_summary":
        print("FAIL: last-hour prompt did not route correctly")
        return 1
    print("PASS: dashboard agent routing and read-only tool execution are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
