import math, sys
sys.path.insert(0, f"{sys.argv[1]}/ref-rrsi")
from rrsi.schedule import edit_budget, budget_table
from rrsi.selection import cost_rule
from rrsi.config import RRSIConfig

# --- RRSI Table 6 checks with coding config
cod = RRSIConfig(beta0=0.10, beta1=44.5, w_s=0.0, w_c=15.0, w_n=0.5)
eng = RRSIConfig(beta0=0.15, beta1=24.4, w_s=244.0, w_c=2.0, w_n=0.5)
print("R0-A", cost_rule(7/178, 0.0, 0, 0.017, cod))
for nu in range(5):
    print("R0-B nu=",nu, cost_rule(3/178, 0.261, nu, 0.017, cod)[0])
print("ENG-R2", cost_rule(6/244, 0.016, 0, 0.020, eng))
# unit conversions
print("delta coding", 3/178, "eng", 5/244, "ws", 60/14100)
print("beta1 coding", 0.25*178, "eng", 0.10*244)
print("budget T=20 1..4", budget_table(20,1,4))
print("budget t=T", edit_budget(20,20,1,4))
# raw value at t=19
print("raw t=19", 1+3*0.5*(1+math.cos(math.pi*19/20)))

# --- Dream-RSI Eq.1 toy world
def V(best, N, k, b1, b2): return best - b1*N + b2*N/max(1,k)
print("V_A", V(0.62,3,3,0.01,0.02), "V_B", V(0.70,4,2,0.01,0.02))
print("pen A", 3/3, "pen B", 2/4)
base, ceil = 0.40, 0.70
print("qA", (0.62-base)/(ceil-base), "qB", (0.70-base)/(ceil-base), "uA", 3/6, "uB", 4/6)
# pareto auc for single world with two beta points: A at (0.5,0.733), B at (0.667,1.0)
pts=[(3/6,(0.62-base)/(ceil-base)),(4/6,1.0)]
def auc(pts):
    xs=sorted(set([0.0,1.0]+[u for u,_ in pts]))
    area=0.0
    for a,b in zip(xs,xs[1:]):
        best=max([q for u,q in pts if u<=a] or [0.0])
        area+=best*(b-a)
    return area
print("AUC", auc(pts))
