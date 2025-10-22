{\rtf1\ansi\ansicpg1252\cocoartf2865
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 from flask import Flask\
from threading import Thread\
\
app = Flask(__name__)\
\
@app.get("/")\
def root():\
    return "HyperWhaleBot alive", 200\
\
@app.get("/health")\
def health():\
    return \{"status": "ok"\}, 200\
\
def _run():\
    # Replit expone el puerto autom\'e1ticamente; Flask usa 0.0.0.0\
    app.run(host="0.0.0.0", port=8080)\
\
def keep_alive():\
    t = Thread(target=_run, daemon=True)\
    t.start()}