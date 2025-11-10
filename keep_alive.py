from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.get("/")
def root():
    return "HyperWhaleBot alive", 200

@app.get("/health")
def health():
    return {"status": "ok"}, 200

def _run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=_run, daemon=True)
    t.start()
