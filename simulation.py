#!/usr/bin/env python3
"""90% Projesi - Model B simülasyonu (yapısal test, tahmin değil).

Kurallar: girişte kişi başı 1 token basılır, hizmet ödemesi token'ı alıcıdan
sağlayıcıya geçirir (yeni token basılmaz). Fiyat varsayılan: 1 saat = 1 token.
Her hafta 4 tur: herkes sırayla rastgele birinden 1 saatlik hizmet almaya çalışır.
Potansiyel hacim = N x 4 saat/hafta. Herkes aynı kapasite ve aynı talebe sahip,
eşleşme rastgele: sonuçlar yapının davranışını gösterir, gerçek hayatı tahmin etmez.

Parametreler:
  savers  : biriktirenlerin oranı (bakiyeleri 2 token'ın altına düşecekse harcamazlar)
  credit  : izin verilen borç (0 = peşin ödeme)
  outflow : her hafta pozitif bakiyenin dışarıda harcanıp yanan oranı
  flex    : True ise fiyat arzla orantılı değişir (arz yarıya inerse fiyat yarıya iner)

Çalıştır: python3 simulation.py
"""
import random

SAVER_RESERVE = 2.0

def gini(xs):
    xs = sorted(xs); n = len(xs); s = sum(xs)
    if s <= 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return 2 * cum / (n * s) - (n + 1) / n

def run(N=100, weeks=104, rounds=4, savers=0.0, credit=0.0, outflow=0.0, flex=False, seed=1):
    rnd = random.Random(seed)
    bal = [1.0] * N                          # girişte 1 token basıldı
    reserve = [SAVER_RESERVE if i < int(N * savers) else 0.0 for i in range(N)]
    M0 = float(N)
    vols = []
    for _ in range(weeks):
        cap = [float(rounds)] * N            # haftalık hizmet kapasitesi (saat)
        p = max(sum(bal), 1e-9) / M0 if flex else 1.0   # token / saat
        traded = 0
        for _r in range(rounds):
            order = list(range(N)); rnd.shuffle(order)
            for b in order:
                s = rnd.randrange(N)
                if s == b or cap[s] < 1:
                    continue
                if bal[b] - p < reserve[b] - credit - 1e-9:
                    continue
                bal[b] -= p; bal[s] += p; cap[s] -= 1; traded += 1
        vols.append(traded)
        if outflow:
            bal = [x * (1 - outflow) if x > 0 else x for x in bal]
    tail = vols[-26:]
    p_end = max(sum(bal), 1e-9) / M0 if flex else 1.0
    blocked = sum(1 for i in range(N) if bal[i] - p_end < reserve[i] - credit - 1e-9) / N
    return {
        "hacim": sum(tail) / len(tail) / (N * rounds),
        "alamayan": blocked,
        "gini": gini([max(x, 0) for x in bal]),
        "arz": sum(bal) / M0,
        "guc": 1 / p_end,
    }

def show(title, **kw):
    r = run(**kw)
    print(f"{title:<44}{r['hacim']*100:>7.0f}%{r['alamayan']*100:>9.0f}%{r['gini']:>7.2f}{r['arz']*100:>7.0f}%{r['guc']:>7.2f}x")

if __name__ == "__main__":
    print(f"{'senaryo (2 yıl)':<44}{'hacim':>8}{'alamayan':>10}{'gini':>7}{'arz':>8}{'güç':>8}")
    print("hacim: potansiyel işlemin yüzdesi | alamayan: şu an alım yapamayanların oranı")
    print("arz: başlangıca göre token | güç: 1 token'ın satın alma gücü (1.00x = başlangıç)")
    print("-" * 88)
    show("Peşin, 100 kişi (taban)")
    show("Peşin, 10 kişi", N=10)
    show("Peşin, 1000 kişi", N=1000)
    show("Peşin, %30 biriktiren", savers=0.3)
    show("Kredili (-2 limit)", credit=2.0)
    show("Kredili (-2 limit), %30 biriktiren", credit=2.0, savers=0.3)
    show("Çıkış %0.5/hafta, sabit fiyat", outflow=0.005)
    show("Çıkış %0.5/hafta, esnek fiyat", outflow=0.005, flex=True)
    show("Çıkış %0.5/hafta, esnek fiyat, %30 biriktiren", outflow=0.005, flex=True, savers=0.3)