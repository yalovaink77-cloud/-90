#!/usr/bin/env python3
"""90% Project - karşılıklı kredi defteri (prototip v0).

Kurallar (değişmezler):
  1. Birim: 1 saat = 1 kredi. Token basımı yok, yakım yok.
  2. Her işlem bir üyeden diğerine geçer: tüm bakiyelerin toplamı HER ZAMAN 0.
  3. Bir üye -limit saatin altına düşemez (varsayılan 10).
  4. İşlem iki taraflı: hizmeti veren önerir, alan onaylar. Onaysız bakiye değişmez.
  5. Defter sadece eklenir; her kayıt öncekinin hash'ine bağlı (kurcalama görünür).
"""
import argparse, hashlib, json, sqlite3, sys, time

DB = "ledger.db"
DEFAULT_LIMIT = 10.0

def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = con()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS members(
        name TEXT PRIMARY KEY, limit_hours REAL NOT NULL, joined REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS proposals(
        id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, receiver TEXT,
        hours REAL, memo TEXT, status TEXT DEFAULT 'pending', ts REAL);
    CREATE TABLE IF NOT EXISTS entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER,
        provider TEXT, receiver TEXT, hours REAL, memo TEXT, ts REAL,
        prev_hash TEXT, hash TEXT);
    """)
    c.commit()

def balance(c, name):
    earned = c.execute("SELECT COALESCE(SUM(hours),0) FROM entries WHERE provider=?", (name,)).fetchone()[0]
    spent = c.execute("SELECT COALESCE(SUM(hours),0) FROM entries WHERE receiver=?", (name,)).fetchone()[0]
    return earned - spent

def entry_hash(prev, p, r, h, memo, ts):
    return hashlib.sha256(json.dumps([prev, p, r, h, memo, ts]).encode()).hexdigest()

def join(name, limit):
    c = con()
    c.execute("INSERT INTO members VALUES(?,?,?)", (name, limit, time.time()))
    c.commit()
    print(f"+ {name} katıldı (kredi limiti: -{limit}s)")

def propose(provider, receiver, hours, memo):
    c = con()
    if provider == receiver:
        sys.exit("Hata: kendine işlem yapamazsın.")
    if hours <= 0:
        sys.exit("Hata: saat pozitif olmalı.")
    for n in (provider, receiver):
        if not c.execute("SELECT 1 FROM members WHERE name=?", (n,)).fetchone():
            sys.exit(f"Hata: üye yok: {n}")
    cur = c.execute("INSERT INTO proposals(provider,receiver,hours,memo,ts) VALUES(?,?,?,?,?)",
                    (provider, receiver, hours, memo, time.time()))
    c.commit()
    print(f"Öneri #{cur.lastrowid}: {provider} -> {receiver}, {hours}s, '{memo}' (onay bekliyor)")

def confirm(pid, by):
    c = con()
    p = c.execute("SELECT * FROM proposals WHERE id=? AND status='pending'", (pid,)).fetchone()
    if not p:
        sys.exit("Hata: bekleyen öneri yok.")
    if by != p["receiver"]:
        sys.exit("Hata: sadece hizmeti alan onaylayabilir.")
    lim = c.execute("SELECT limit_hours FROM members WHERE name=?", (p["receiver"],)).fetchone()[0]
    if balance(c, p["receiver"]) - p["hours"] < -lim:
        sys.exit(f"Hata: {p['receiver']} kredi limitini aşar (-{lim}s).")
    last = c.execute("SELECT hash FROM entries ORDER BY id DESC LIMIT 1").fetchone()
    prev = last[0] if last else "GENESIS"
    ts = time.time()
    h = entry_hash(prev, p["provider"], p["receiver"], p["hours"], p["memo"], ts)
    c.execute("INSERT INTO entries(proposal_id,provider,receiver,hours,memo,ts,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?)",
              (pid, p["provider"], p["receiver"], p["hours"], p["memo"], ts, prev, h))
    c.execute("UPDATE proposals SET status='confirmed' WHERE id=?", (pid,))
    c.commit()
    print(f"✓ #{pid} onaylandı.")

def reject(pid, by):
    c = con()
    p = c.execute("SELECT * FROM proposals WHERE id=? AND status='pending'", (pid,)).fetchone()
    if not p or by != p["receiver"]:
        sys.exit("Hata: bekleyen öneri yok veya yetkin yok.")
    c.execute("UPDATE proposals SET status='rejected' WHERE id=?", (pid,))
    c.commit()
    print(f"✗ #{pid} reddedildi.")

def balances():
    c = con()
    print(f"{'üye':<15}{'bakiye':>9}{'limit':>8}")
    for m in c.execute("SELECT * FROM members ORDER BY name"):
        print(f"{m['name']:<15}{balance(c, m['name']):>9.2f}{-m['limit_hours']:>8.1f}")

def audit():
    c = con()
    total = sum(balance(c, m["name"]) for m in c.execute("SELECT name FROM members"))
    prev, ok = "GENESIS", True
    for e in c.execute("SELECT * FROM entries ORDER BY id"):
        exp = entry_hash(prev, e["provider"], e["receiver"], e["hours"], e["memo"], e["ts"])
        if e["prev_hash"] != prev or e["hash"] != exp:
            ok = False
            print(f"! zincir kırık: kayıt #{e['id']}")
        prev = e["hash"]
    print(f"Bakiye toplamı: {total:.2f} (0 olmalı) -> {'OK' if abs(total) < 1e-9 else 'HATA'}")
    print(f"Hash zinciri: {'OK' if ok else 'HATA'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = ap.add_subparsers(dest="cmd", required=True)
    a = s.add_parser("join"); a.add_argument("name"); a.add_argument("--limit", type=float, default=DEFAULT_LIMIT)
    a = s.add_parser("propose"); a.add_argument("provider"); a.add_argument("receiver")
    a.add_argument("hours", type=float); a.add_argument("memo", nargs="?", default="")
    a = s.add_parser("confirm"); a.add_argument("id", type=int); a.add_argument("by")
    a = s.add_parser("reject"); a.add_argument("id", type=int); a.add_argument("by")
    s.add_parser("balances"); s.add_parser("audit")
    args = ap.parse_args()
    init()
    {"join": lambda: join(args.name, args.limit),
     "propose": lambda: propose(args.provider, args.receiver, args.hours, args.memo),
     "confirm": lambda: confirm(args.id, args.by),
     "reject": lambda: reject(args.id, args.by),
     "balances": balances, "audit": audit}[args.cmd]()