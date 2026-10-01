import random, math, sys, tempfile
from pathlib import Path
sys.path.insert(0, f"{sys.argv[1]}/ref-rrsi")
from rrsi.evaluate import TaskResult, aggregate
from rrsi.selection import Candidate, select_round
from rrsi.history import History
from rrsi.config import RRSIConfig
from rrsi.components import K

rng = random.Random(0)
cfg = RRSIConfig(beta0=0.1, beta1=40, w_s=100, w_c=15, w_n=0.5)
checked = stopped = 0
for trial in range(4000):
    n_tasks, k = rng.randint(3, 12), rng.randint(1, 3)
    def ev(p):
        per = {f"t{i}": TaskResult(rewards=[1.0 if rng.random() < p else 0.0 for _ in range(k)], tokens=[1000.0]*k) for i in range(n_tasks)}
        return aggregate("x", k, per)
    inc = ev(rng.random())
    S_star = min(1.0, inc.S + rng.choice([0.0, rng.random()*0.3]))
    delta = rng.choice([0.0, 0.02, 0.05, 0.1])
    # candidate: evaluate in fixed order, check stop condition after each trial
    p = rng.random()
    full = [[1.0 if rng.random() < p else 0.0 for _ in range(k)] for _ in range(n_tasks)]
    order = [(i, j) for i in range(n_tasks) for j in range(k)]
    A = B = 0.0; R = float(len(order)); stop_at = None
    for idx, (i, j) in enumerate(order):
        A += full[i][j]; B += 1; R -= 1
        Smax = (A + R) / (B + R)
        if Smax < min(S_star - delta, inc.S):
            stop_at = idx; break
    checked += 1
    if stop_at is None: continue
    stopped += 1
    full_ev = aggregate("c", k, {f"t{i}": TaskResult(rewards=full[i], tokens=[1000.0]*k) for i in range(n_tasks)})
    comp = rng.choice(K)
    other = Candidate("A", [{"id": "C1", "component": rng.choice(K)}], ev=ev(rng.random()))
    candF = Candidate("B", [{"id": "C1", "component": comp}], ev=full_ev)
    wF, dF = select_round([other, candF], inc, S_star, delta, cfg, {})
    # early-stopped: candidate excluded from admissible (floor fail certain)
    assert full_ev.S < S_star - delta, "floor must fail"
    wE, dE = select_round([other], inc, S_star, delta, cfg, {})
    assert (wF.variant if wF else None) == (wE.variant if wE else None), "winner differs"
    # history: prior random records + this record (full ΔS vs upper bound)
    with tempfile.TemporaryDirectory() as td:
        hF, hE = History(Path(td)/"f.jsonl"), History(Path(td)/"e.jsonl")
        for _ in range(rng.randint(0, 6)):
            rec = dict(t=rng.randint(0, 5), variant="Z", edits=[{"id":"C1","component":rng.choice(K),"hypothesis":"h"}],
                       outcome="REJECTED", delta_S=rng.uniform(-0.1, 0.1), delta_C=0.0, accepted=False, S=0.5, C=1000, diff=None)
            hF.append_candidate(**rec); hE.append_candidate(**rec)
        t = 6
        dS_full = full_ev.S - inc.S
        dS_bound = Smax - inc.S
        assert dS_full <= dS_bound < 0
        hF.append_candidate(t, "B", [{"id":"C1","component":comp,"hypothesis":"x"}], "REJECTED", dS_full, 0.0, False, full_ev.S, 1000, None)
        hE.append_candidate(t, "B", [{"id":"C1","component":comp,"hypothesis":"x"}], "REJECTED", dS_bound, 0.0, False, Smax, 1000, None)
        for tt in range(t, t+6):
            for npr in (2, 4):
                pF = {x["component"] for x in hF.prune_set(tt, npr)}
                pE = {x["component"] for x in hE.prune_set(tt, npr)}
                assert pF == pE, (pF, pE)
        assert hF.tried() == hE.tried()
        assert hF.incumbent_component_counts() == hE.incumbent_component_counts()
print(f"checked={checked} early_stopped={stopped} all invariants held")
