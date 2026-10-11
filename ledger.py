#!/usr/bin/env python3
"""90% Projesi - token defteri (prototip v0.2).

Değişmezler:
  1. Üye girince 1 token basılır (mint). Üye ayrılınca bakiyesinin tamamı yanar (burn).
     Çalışmak token basmaz: hizmet ödemesi token'ı alıcıdan sağlayıcıya geçirir.
     Dolaşımdaki token = basılan - yanan. Dışarı satış/alış yoktur.
  2. Bakiye, sponsor destekli kredi limiti kadar negatif olabilir.
  3. İşlem iki taraflı: hizmeti veren önerir, alan onaylar.
  4. Defter sadece eklenir; her kayıt öncekinin hash'ine bağlıdır.
  5. Her aktif üyenin her oylamada tek oyu vardır. Oy satılamaz, devredilemez.
  6. Üyelik: kurucu ilk üyeleri (genesis) ekler, sonrası sponsorla girer. Limit, farklı
     işlem partneri sayısıyla artar. Sponsor, koruduğu üyelerin borcu kadar limit kaybeder.
"""
import argparse, hashlib, json, sqlite3, sys, time

DB = "ledger.db"
SYSTEM = "SYSTEM"

# --- Parametreler (pilotta ayarlanacak) ---
GENESIS_MAX = 10             # kurucunun doğrudan ekleyebileceği üye sayısı
GENESIS_LIMIT = 10.0         # genesis üyelerin azami borç limiti
START_LIMIT = 2.0            # sponsorlu yeni üyenin başlangıç borç limiti
MAX_LIMIT = 10.0             # sponsorlu üyenin ulaşabileceği azami borç limiti
MAX_SPONSORED = 3            # bir üyenin sponsor olabileceği kişi sayısı
MIN_PARTNERS_TO_SPONSOR = 2  # sponsor olmak için gereken farklı işlem partneri

def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = con()
    has = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='members'").fetchone()
    if has and "active" not in [r[1] for r in c.execute("PRAGMA table_info(members)")]:
        sys.exit("Hata: ledger.db eski sürüm. Test verisiyse silip yeniden başla: rm -f ledger.db")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS members(
        name TEXT PRIMARY KEY, limit_hours REAL NOT NULL, joined REAL NOT NULL,
        sponsor TEXT, genesis INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS proposals(
        id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, receiver TEXT,
        hours REAL, memo TEXT, status TEXT DEFAULT 'pending', ts REAL);
    CREATE TABLE IF NOT EXISTS entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER, kind TEXT,
        provider TEXT, receiver TEXT, hours REAL, memo TEXT, ts REAL,
        prev_hash TEXT, hash TEXT);
    CREATE TABLE IF NOT EXISTS polls(
        id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS votes(
        poll_id INTEGER, member TEXT, choice TEXT, ts REAL, PRIMARY KEY(poll_id, member));
    """)
    c.commit()

# ---------- defter çekirdeği ----------
def balance(c, name):
    earned = c.execute("SELECT COALESCE(SUM(hours),0) FROM entries WHERE provider=?", (name,)).fetchone()[0]
    spent = c.execute("SELECT COALESCE(SUM(hours),0) FROM entries WHERE receiver=?", (name,)).fetchone()[0]
    return earned - spent

def entry_hash(prev, kind, p, r, h, memo, ts):
    return hashlib.sha256(json.dumps([prev, kind, p, r, h, memo, ts]).encode()).hexdigest()

def append(c, kind, provider, receiver, hours, memo="", pid=None):
    """provider token alır, receiver token öder. mint: SYSTEM -> üye, burn: üye -> SYSTEM."""
    last = c.execute("SELECT hash FROM entries ORDER BY id DESC LIMIT 1").fetchone()
    prev = last[0] if last else "GENESIS"
    ts = time.time()
    h = entry_hash(prev, kind, provider, receiver, hours, memo, ts)
    c.execute("""INSERT INTO entries(proposal_id,kind,provider,receiver,hours,memo,ts,prev_hash,hash)
                 VALUES(?,?,?,?,?,?,?,?,?)""", (pid, kind, provider, receiver, hours, memo, ts, prev, h))

def supply(c):
    m = c.execute("SELECT COALESCE(SUM(hours),0) FROM entries WHERE kind='mint'").fetchone()[0]
    b = c.execute("SELECT COALESCE(SUM(hours),0) FROM entries WHERE kind='burn'").fetchone()[0]
    return m - b

def member(c, name):
    return c.execute("SELECT * FROM members WHERE name=?", (name,)).fetchone()

def active_member(c, name):
    m = member(c, name)
    return m if m and m["active"] else None

def partners(c, name):
    return c.execute("""SELECT COUNT(*) FROM (
        SELECT receiver FROM entries WHERE kind='trade' AND provider=?
        UNION SELECT provider FROM entries WHERE kind='trade' AND receiver=?)""", (name, name)).fetchone()[0]

def effective_limit(c, name):
    m = member(c, name)
    lim = GENESIS_LIMIT if m["genesis"] else min(MAX_LIMIT, START_LIMIT + partners(c, name))
    debt = sum(max(0.0, -balance(c, s["name"]))
               for s in c.execute("SELECT name FROM members WHERE sponsor=?", (name,)))
    return max(0.0, lim - debt)

# ---------- üyelik ----------
def join(name, genesis, sponsor):
    c = con()
    if name == SYSTEM or member(c, name):
        sys.exit("Hata: bu isim kullanılamaz.")
    if genesis:
        if sponsor:
            sys.exit("Hata: genesis üyenin sponsoru olmaz.")
        n = c.execute("SELECT COUNT(*) FROM members WHERE genesis=1 AND active=1").fetchone()[0]
        if n >= GENESIS_MAX:
            sys.exit(f"Hata: genesis kontenjanı doldu ({GENESIS_MAX}).")
    else:
        if not sponsor:
            sys.exit("Hata: --sponsor ISIM gerekli (ya da kurucu için --genesis).")
        s = active_member(c, sponsor)
        if not s:
            sys.exit(f"Hata: aktif sponsor yok: {sponsor}")
        if not s["genesis"] and partners(c, sponsor) < MIN_PARTNERS_TO_SPONSOR:
            sys.exit(f"Hata: sponsor olmak için en az {MIN_PARTNERS_TO_SPONSOR} farklı kişiyle işlem gerekir.")
        if c.execute("SELECT COUNT(*) FROM members WHERE sponsor=? AND active=1", (sponsor,)).fetchone()[0] >= MAX_SPONSORED:
            sys.exit(f"Hata: {sponsor} en fazla {MAX_SPONSORED} kişiye sponsor olabilir.")
        if effective_limit(c, sponsor) <= 0:
            sys.exit(f"Hata: {sponsor} koruduğu üyelerin borcu yüzünden sponsor olamaz.")
    c.execute("INSERT INTO members(name,limit_hours,joined,sponsor,genesis,active) VALUES(?,?,?,?,?,1)",
              (name, GENESIS_LIMIT if genesis else START_LIMIT, time.time(), sponsor, 1 if genesis else 0))
    append(c, "mint", name, SYSTEM, 1.0, "giriş")
    c.commit()
    print(f"+ {name} katıldı ({'genesis' if genesis else 'sponsor: ' + sponsor}), 1 token basıldı")

def leave(name):
    c = con()
    if not active_member(c, name):
        sys.exit("Hata: aktif üye yok.")
    bal = balance(c, name)
    if bal < 0:
        sys.exit(f"Hata: {name} {-bal:g} token borçlu. Ayrılmadan önce borç kapanmalı.")
    for s in c.execute("SELECT name FROM members WHERE sponsor=? AND active=1", (name,)):
        if balance(c, s["name"]) < 0:
            sys.exit(f"Hata: koruduğun üye {s['name']} borçlu. Ayrılmadan önce kapanmalı.")
    if bal > 0:
        append(c, "burn", SYSTEM, name, bal, "ayrılış")
    c.execute("UPDATE members SET active=0 WHERE name=?", (name,))
    c.execute("UPDATE proposals SET status='cancelled' WHERE status='pending' AND (provider=? OR receiver=?)", (name, name))
    c.commit()
    print(f"- {name} ayrıldı, {bal:g} token yandı (dolaşım: {supply(c):g})")

# ---------- işlemler ----------
def propose(provider, receiver, hours, memo):
    c = con()
    if provider == receiver:
        sys.exit("Hata: kendine işlem yapamazsın.")
    if hours <= 0:
        sys.exit("Hata: saat pozitif olmalı.")
    for n in (provider, receiver):
        if not active_member(c, n):
            sys.exit(f"Hata: aktif üye yok: {n}")
    cur = c.execute("INSERT INTO proposals(provider,receiver,hours,memo,ts) VALUES(?,?,?,?,?)",
                    (provider, receiver, hours, memo, time.time()))
    c.commit()
    print(f"Öneri #{cur.lastrowid}: {provider} -> {receiver}, {hours:g} token, '{memo}' (onay bekliyor)")

def confirm(pid, by):
    c = con()
    p = c.execute("SELECT * FROM proposals WHERE id=? AND status='pending'", (pid,)).fetchone()
    if not p:
        sys.exit("Hata: bekleyen öneri yok.")
    if by != p["receiver"]:
        sys.exit("Hata: sadece hizmeti alan onaylayabilir.")
    if not (active_member(c, p["provider"]) and active_member(c, p["receiver"])):
        sys.exit("Hata: taraflardan biri artık aktif üye değil.")
    lim = effective_limit(c, p["receiver"])
    if balance(c, p["receiver"]) - p["hours"] < -lim:
        sys.exit(f"Hata: {p['receiver']} borç limitini aşar (-{lim:g}).")
    append(c, "trade", p["provider"], p["receiver"], p["hours"], p["memo"], pid)
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

# ---------- oylama (1 üye = 1 oy) ----------
def poll(question):
    c = con()
    cur = c.execute("INSERT INTO polls(question,ts) VALUES(?,?)", (question, time.time()))
    c.commit()
    print(f"Oylama #{cur.lastrowid}: {question}")

def vote(pid, name, choice):
    c = con()
    ch = {"evet": "evet", "hayir": "hayir", "hayır": "hayir"}.get(choice.lower())
    if not ch:
        sys.exit("Hata: seçenek evet ya da hayir olmalı.")
    if not c.execute("SELECT 1 FROM polls WHERE id=?", (pid,)).fetchone():
        sys.exit("Hata: oylama yok.")
    if not active_member(c, name):
        sys.exit("Hata: sadece aktif üyeler oy kullanabilir.")
    try:
        c.execute("INSERT INTO votes VALUES(?,?,?,?)", (pid, name, ch, time.time()))
    except sqlite3.IntegrityError:
        sys.exit("Hata: bu oylamada zaten oy kullandın.")
    c.commit()
    print(f"✓ {name} oyunu kullandı.")

def tally(pid):
    c = con()
    q = c.execute("SELECT question FROM polls WHERE id=?", (pid,)).fetchone()
    if not q:
        sys.exit("Hata: oylama yok.")
    n = lambda ch: c.execute("SELECT COUNT(*) FROM votes WHERE poll_id=? AND choice=?", (pid, ch)).fetchone()[0]
    act = c.execute("SELECT COUNT(*) FROM members WHERE active=1").fetchone()[0]
    e, h = n("evet"), n("hayir")
    print(f"#{pid} {q[0]}\n  evet: {e}  hayır: {h}  katılım: {e + h}/{act} aktif üye")

# ---------- raporlar ----------
def balances():
    c = con()
    print(f"{'üye':<14}{'bakiye':>8}{'borç limiti':>13}  sponsor")
    for m in c.execute("SELECT * FROM members WHERE active=1 ORDER BY name"):
        who = "genesis" if m["genesis"] else (m["sponsor"] or "-")
        print(f"{m['name']:<14}{balance(c, m['name']):>8.2f}{-effective_limit(c, m['name']):>13.1f}  {who}")
    act = c.execute("SELECT COUNT(*) FROM members WHERE active=1").fetchone()[0]
    print(f"Dolaşımdaki token: {supply(c):g} | aktif üye: {act}")

def audit():
    c = con()
    total = sum(balance(c, m["name"]) for m in c.execute("SELECT name FROM members"))
    sup = supply(c)
    prev, ok = "GENESIS", True
    for e in c.execute("SELECT * FROM entries ORDER BY id"):
        exp = entry_hash(prev, e["kind"], e["provider"], e["receiver"], e["hours"], e["memo"], e["ts"])
        if e["prev_hash"] != prev or e["hash"] != exp:
            ok = False
            print(f"! zincir kırık: kayıt #{e['id']}")
        prev = e["hash"]
    stray = c.execute("SELECT COUNT(*) FROM votes WHERE member NOT IN (SELECT name FROM members)").fetchone()[0]
    print(f"Bakiye toplamı: {total:g} | basılan - yanan: {sup:g} -> {'OK' if abs(total - sup) < 1e-9 else 'HATA'}")
    print(f"Hash zinciri: {'OK' if ok else 'HATA'}")
    print(f"Oy bütünlüğü: {'OK' if stray == 0 else 'HATA'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = ap.add_subparsers(dest="cmd", required=True)
    a = s.add_parser("join"); a.add_argument("name"); a.add_argument("--genesis", action="store_true"); a.add_argument("--sponsor")
    a = s.add_parser("leave"); a.add_argument("name")
    a = s.add_parser("propose"); a.add_argument("provider"); a.add_argument("receiver")
    a.add_argument("hours", type=float); a.add_argument("memo", nargs="?", default="")
    a = s.add_parser("confirm"); a.add_argument("id", type=int); a.add_argument("by")
    a = s.add_parser("reject"); a.add_argument("id", type=int); a.add_argument("by")
    a = s.add_parser("poll"); a.add_argument("question")
    a = s.add_parser("vote"); a.add_argument("id", type=int); a.add_argument("name"); a.add_argument("choice")
    a = s.add_parser("tally"); a.add_argument("id", type=int)
    s.add_parser("balances"); s.add_parser("audit")
    args = ap.parse_args()
    init()
    {"join": lambda: join(args.name, args.genesis, args.sponsor),
     "leave": lambda: leave(args.name),
     "propose": lambda: propose(args.provider, args.receiver, args.hours, args.memo),
     "confirm": lambda: confirm(args.id, args.by),
     "reject": lambda: reject(args.id, args.by),
     "poll": lambda: poll(args.question),
     "vote": lambda: vote(args.id, args.name, args.choice),
     "tally": lambda: tally(args.id),
     "balances": balances, "audit": audit}[args.cmd]()