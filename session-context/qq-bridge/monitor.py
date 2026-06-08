"""Monitor — tail consumer.log, signal Hub when tasks arrive. Event-driven, zero polling."""
import os, sys, time

DIR = os.environ.get("BRIDGE_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(DIR, "consumer.log")

print("Monitor started, waiting for consumer.log...", flush=True)

# Wait for log file to appear
while not os.path.exists(LOG_FILE):
    time.sleep(0.5)

print("Monitor watching consumer.log", flush=True)

try:
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # seek to end
        while True:
            line = f.readline()
            if line:
                sys.stdout.write(line.rstrip() + "\n")
                sys.stdout.flush()
                if "queued for Claude" in line:
                    print("TASK_DETECTED", flush=True)
            else:
                time.sleep(0.5)
except KeyboardInterrupt:
    pass
except Exception as e:
    print(f"Monitor error: {e}", flush=True)
