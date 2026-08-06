# The public face of the Builder: the landing page IS the console, and every visitor
# gets a guest session with their own starter repository.
#
# GATE_TRUST_PROXY is set here rather than left to configuration because it states a
# fact about this deployment's own topology: a platform proxy terminates TLS in front
# of us, so the socket peer is the proxy and X-Forwarded-For is the visitor. Without
# it every guest counts as one address and they share a single per-address ceiling.
#
# Ceilings default to demo-safe (3 runs/session, 6/address/h, 25/instance/h). Raise
# them with GATE_MAX_RUNS, GATE_MAX_RUNS_PER_IP, GATE_MAX_RUNS_PER_HOUR. Set
# GATE_SECRET so a redeploy does not sign every guest out, and GOV_DATA_DIR to a
# volume so the gate and the ledger survive one. The port comes from $PORT.
#
# The settlement console has not gone anywhere:  python3 gov/sim_console.py --seed
web: GATE_TRUST_PROXY=1 python gov/gate.py --serve --host 0.0.0.0
