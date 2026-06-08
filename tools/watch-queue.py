"""Watch consumer.log for new 'queued for Claude' lines.
Usage: python watch-queue.py [--bot <bot_name>]
"""
import os, time, sys, argparse

DATA_DIR = os.environ.get("BRIDGE_DATA_DIR", "")
if DATA_DIR:
    LOG_FILE = os.path.join(DATA_DIR, "consumer.log")
else:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_log = os.path.join(ROOT, "session-context", "qq-bridge", "consumer.log")
    HUB_FALLBACK = r"D:\claude-hub\session-context\qq-bridge"
    hub_log = os.path.join(HUB_FALLBACK, "consumer.log")
    LOG_FILE = hub_log if os.path.exists(hub_log) else local_log

parser = argparse.ArgumentParser()
parser.add_argument("--bot", type=str, default="", help="Filter by bot name")
args = parser.parse_args()

bot_tag = f"[{args.bot}]" if args.bot else ""

while not os.path.exists(LOG_FILE):
    time.sleep(0.5)

with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
    f.seek(0, 2)
    while True:
        line = f.readline()
        if line:
            if "queued for Claude" in line:
                if not bot_tag or bot_tag in line:
                    try:
                        sys.stdout.write(line)
                    except UnicodeEncodeError:
                        sys.stdout.write(line.encode("ascii", errors="replace").decode())
                    sys.stdout.flush()
        else:
            time.sleep(0.5)
