#!/usr/bin/env python3
"""90% Project - karşılıklı kredi defteri (prototip v0.1).

Kurallar (değişmezler):
  1. Birim: 1 saat = 1 kredi. Token basımı yok, yakım yok.
  2. Her işlem bir üyeden diğerine geçer: tüm bakiyelerin toplamı HER ZAMAN 0.
  3. Bir üye -limit saatin altına düşemez (varsayılan 10).
  4. İşlem iki taraflı: hizmeti veren önerir, alan onaylar. Onaysız bakiye değişmez.
  5. Defter sadece eklenir; her kayıt öncekinin hash'ine bağlı (kurcalama görünür).
  6. Üyelik: kurucu ilk üyeleri (genesis) ekler; sonrası sponsorla girer.
     Sponsorlu üye düşük limitle başlar, farklı kişilerle işlem yaptıkça limiti artar.
     Sponsor, koruduğu üyelerin borcu kadar kendi limitinden kaybeder.
"""
import argparse, hashlib, json, sqlite3, sys, time

DB = "ledger.db"

# --- Parametreler (pilotta ayarlanacak) ---
GENESIS_MAX = 10             # kurucunun doğrudan ekleyebileceği üye sayısı
GENESIS_LIMIT = 10.0         # genesis üyelerin kredi limiti (saat)
START_LIMIT = 2.0            # sponsorlu yeni üyenin başlangıç limiti
MAX_LIMIT = 10.0             # sponsorlu üyenin ulaşabileceği azami limit
MAX_SPONSORED = 3            # bir üyenin sponsor olabileceği kişi sayısı
MIN_PARTNERS_TO_SPONSOR = 2  # sponsor olmak için gereken farklı işlem partneri

def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = con()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS members(
        name TEXT PRIMARY KEY, limit_hours REAL NOT NULL, joined REAL NOT NULL,
        sponsor TEXT, genesis INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS proposals(
        id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, receiver TEXT,
        hours REAL, memo TEXT, status TEXT DEFAULT 'pending', ts REAL);
    CREATE TABLE IF NOT EXISTS entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER,
        provider TEXT, receiver TEXT, hours REAL, memo TEXT, ts REAL,
        prev_hash TEXT, hash TEXT);
    """)
    cols = [r[1] for r in c.execute("PRAGMA table_info(members)")]
    if "sponsor" not in cols:
        c.execute("ALTER TABLE members ADD COLUMN sponsor TEXT")
    if "genesis" not in cols:
        c.execute("ALTER TABLE members ADD COLUMN genesis INTEGER DEFAULT 0")
    c.commit()

def balance(c, name):
    earned = c.execute("SELECT COALESCE(SUM(hours),0) FROM entries WHERE provider=?", (name,)).fetchone()[0]
    spent = c.execute("SELECT COALESCE(SUM(hours),0) FROM entries WHERE receiver=?", (name,)).fetchone()[0]
    return earned - spent

def entry_hash(prev, p, r, h, memo, ts):
    return hashlib.sha256(json.dumps([prev, p, r, h, memo, ts]).encode()).hexdigest()

def member(c, name):
    return c.execute("SELECT * FROM members WHERE name=?", (name,)).fetchone()

def partners(c, name):
    return c.execute("""SELECT COUNT(*) FROM (
        SELECT receiver FROM entries WHERE provider=?
        UNION SELECT provider FROM entries WHERE receiver=?)""", (name, name)).fetchone()[0]

def effective_limit(c, name):
    m = member(c, name)
    lim = GENESIS_LIMIT if m["genesis"] else min(MAX_LIMIT, START_LIMIT + partners(c, name))
    debt = sum(max(0.0, -balance(c, s["name"]))
               for s in c.execute("SELECT name FROM members WHERE sponsor=?", (name,)))
    return max(0.0, lim - debt)

def join(name, genesis, sponsor):
    c = con()
    if member(c, name):
        sys.exit("Hata: üye zaten var.")
    if genesis:
        if sponsor:
            sys.exit("Hata: genesis üyenin sponsoru olmaz.")
        n = c.execute("SELECT COUNT(*) FROM members WHERE genesis=1").fetchone()[0]
        if n >= GENESIS_MAX:
            sys.exit(f"Hata: genesis kontenjanı doldu ({GENESIS_MAX}).")
    else:
        if not sponsor:
            sys.exit("Hata: --sponsor ISIM gerekli (ya da kurucu için --genesis).")
        s = member(c, sponsor)
        if not s:
            sys.exit(f"Hata: sponsor yok: {sponsor}")
        if not s["genesis"] and partners(c, sponsor) < MIN_PARTNERS_TO_SPONSOR:
            sys.exit(f"Hata: sponsor olmak için en az {MIN_PARTNERS_TO_SPONSOR} farklı kişiyle işlem gerekir.")
        if c.execute("SELECT COUNT(*) FROM members WHERE sponsor=?", (sponsor,)).fetchone()[0] >= MAX_SPONSORED:
            sys.exit(f"Hata: {sponsor} en fazla {MAX_SPONSORED} kişiye sponsor olabilir.")
        if effective_limit(c, sponsor) <= 0:
            sys.exit(f"Hata: {sponsor} koruduğu üyelerin borcu yüzünden sponsor olamaz.")
    c.execute("INSERT INTO members(name,limit_hours,joined,sponsor,genesis) VALUES(?,?,?,?,?)",
              (name, GENESIS_LIMIT if genesis else START_LIMIT, time.time(), sponsor, 1 if genesis else 0))
    c.commit()
    print(f"+ {name} katıldı ({'genesis' if genesis else 'sponsor: ' + sponsor})")

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
    lim = effective_limit(c, p["receiver"])
    if balance(c, p["receiver"]) - p["hours"] < -lim:
        sys.exit(f"Hata: {p['receiver']} kredi limitini aşar (-{lim:g}s).")
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
    print(f"{'üye':<14}{'bakiye':>8}{'limit':>8}  sponsor")
    for m in c.execute("SELECT * FROM members ORDER BY name"):
        who = "genesis" if m["genesis"] else (m["sponsor"] or "-")
        print(f"{m['name']:<14}{balance(c, m['name']):>8.2f}{-effective_limit(c, m['name']):>8.1f}  {who}")

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
    a = s.add_parser("join"); a.add_argument("name"); a.add_argument("--genesis", action="store_true"); a.add_argument("--sponsor")
    a = s.add_parser("propose"); a.add_argument("provider"); a.add_argument("receiver")
    a.add_argument("hours", type=float); a.add_argument("memo", nargs="?", default="")
    a = s.add_parser("confirm"); a.add_argument("id", type=int); a.add_argument("by")
    a = s.add_parser("reject"); a.add_argument("id", type=int); a.add_argument("by")
    s.add_parser("balances"); s.add_parser("audit")
    args = ap.parse_args()
    init()
    {"join": lambda: join(args.name, args.genesis, args.sponsor),
     "propose": lambda: propose(args.provider, args.receiver, args.hours, args.memo),
     "confirm": lambda: confirm(args.id, args.by),
     "reject": lambda: reject(args.id, args.by),
     "balances": balances, "audit": audit}[args.cmd]()