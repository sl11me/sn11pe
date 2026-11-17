"""sn11pe — copy-trading de wallets memecoins sur Solana.

Boucle : surveille les wallets "leaders" -> détecte leurs achats/ventes -> copie via Jupiter.
Paper trading par défaut (LIVE=1 pour de vrais ordres). Tableau de bord sur http://localhost:8081.
"""
import base64, json, os, sys, threading, time, urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RPC = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
JUP = "https://api.jup.ag"
JUP_KEY = os.getenv("JUP_API_KEY", "")
LIVE = os.getenv("LIVE") == "1"
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8081"))  # 8080 = Brigitte ; 0 coupe la page
SOL = "So11111111111111111111111111111111111111112"
IGNORED = {  # stables : pas des memecoins
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
STATE_FILE = "state.json"
STARTED = time.time()
LOGS = deque(maxlen=300)    # pour la page ; le terminal garde tout
EVENTS = deque(maxlen=300)  # signaux des leaders et décision prise
SYMBOLS = {}                # cache mint -> symbole
snapshot = "{}"             # dernier état publié pour la page (chaîne : lecture sans verrou)


def http(url, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, json.dumps(body).encode() if body else None, h)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def rpc(method, *params):
    res = http(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)})
    if "error" in res:
        raise RuntimeError(res["error"])
    return res["result"]


def jup(path, body=None):
    return http(JUP + path, body, {"x-api-key": JUP_KEY} if JUP_KEY else {})


def token_info(mint):
    try:
        return (jup(f"/tokens/v2/search?query={mint}") or [{}])[0]
    except Exception:
        return {}


def symbol(mint):
    if mint not in SYMBOLS:
        SYMBOLS[mint] = token_info(mint).get("symbol") or ""
    return SYMBOLS[mint]


# ---------- détection des swaps du leader ----------

def parse_swaps(tx, wallet):
    """Retourne [("buy"|"sell", mint)] à partir d'une transaction jsonParsed."""
    meta = tx and tx.get("meta")
    if not meta or meta.get("err"):
        return []
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    if wallet not in keys:
        return []
    i = keys.index(wallet)
    sol_delta = meta["postBalances"][i] - meta["preBalances"][i]
    tok = {}
    for sign, field in ((-1, "preTokenBalances"), (1, "postTokenBalances")):
        for b in meta.get(field) or []:
            if b.get("owner") == wallet:
                tok[b["mint"]] = tok.get(b["mint"], 0) + sign * int(b["uiTokenAmount"]["amount"])
    sol_delta += tok.pop(SOL, 0)  # wSOL compte comme du SOL
    out = []
    for mint, d in tok.items():
        if mint in IGNORED or d == 0:
            continue
        if d > 0 and sol_delta < 0:
            out.append(("buy", mint))
        elif d < 0 and sol_delta > 0:
            out.append(("sell", mint))
    return out


# ---------- auto-apprentissage : score des wallets ----------

def wallet_stats(trades, wallet):
    mine = [t for t in trades if t["wallet"] == wallet]
    pnl = sum(t["sol_out"] - t["sol_in"] for t in mine)
    wins = sum(t["sol_out"] > t["sol_in"] for t in mine)
    return len(mine), pnl, (wins / len(mine) if mine else 0)


def wallet_allowed(trades, wallet, cfg):
    """Un wallet est coupé dès qu'on a assez d'historique et qu'il nous fait perdre."""
    n, pnl, winrate = wallet_stats(trades, wallet)
    return n < cfg["min_trades_to_judge"] or (pnl > 0 and winrate >= cfg["min_winrate"])


# ---------- exécution ----------

def swap(input_mint, output_mint, amount, cfg):
    """Renvoie la quantité reçue (unités de base). Paper = simple quote, Live = signé + envoyé."""
    q = f"/swap/v2/order?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount)}&slippageBps={cfg['slippage_bps']}"
    if not LIVE:
        return int(jup(q)["outAmount"])
    from solders.keypair import Keypair  # import tardif : inutile en paper
    from solders.transaction import VersionedTransaction
    kp = Keypair.from_base58_string(os.environ["PRIVATE_KEY"])
    order = jup(q + f"&taker={kp.pubkey()}")
    if not order.get("transaction"):
        raise RuntimeError(order.get("error") or order.get("errorMessage") or "pas de transaction")
    tx = VersionedTransaction.from_bytes(base64.b64decode(order["transaction"]))
    signed = VersionedTransaction(tx.message, [kp])
    res = jup("/swap/v2/execute", {"signedTransaction": base64.b64encode(bytes(signed)).decode(),
                                   "requestId": order["requestId"]})
    if res.get("status") != "Success":
        raise RuntimeError(f"swap échoué: {res.get('error')} (code {res.get('code')})")
    return int(res["totalOutputAmount"])


def buy(state, cfg, wallet, mint):
    """Renvoie la décision prise, affichée sur la page."""
    if mint in state["positions"]:
        return "déjà en position"
    if len(state["positions"]) >= cfg["max_positions"]:
        return "capacité max"
    if not wallet_allowed(state["trades"], wallet, cfg):
        log(f"skip {mint[:6]} : wallet {wallet[:6]} désactivé (mauvais score)")
        return "wallet désactivé"
    lamports = int(cfg["buy_sol"] * 1e9)
    tokens = swap(SOL, mint, lamports, cfg)
    state["positions"][mint] = {"wallet": wallet, "symbol": symbol(mint), "sol_in": lamports, "tokens": tokens,
                                "opened": time.time(), "value": lamports, "peak": lamports}
    log(f"[buy] {symbol(mint) or mint} {cfg['buy_sol']} SOL (copie de {wallet[:6]})")
    return "copié"


def sell(state, cfg, mint, reason):
    p = state["positions"].pop(mint)
    sol_out = swap(mint, SOL, p["tokens"], cfg)
    state["trades"].append({"wallet": p["wallet"], "mint": mint, "symbol": p.get("symbol", ""), "sol_in": p["sol_in"],
                            "sol_out": sol_out, "reason": reason, "opened": p["opened"], "closed": time.time()})
    log(f"[sell] {p.get('symbol') or mint} {reason} pnl={(sol_out - p['sol_in']) / 1e9:+.4f} SOL")


def check_exits(state, cfg):
    for mint, p in list(state["positions"].items()):
        try:
            value = int(jup(f"/swap/v2/order?inputMint={mint}&outputMint={SOL}&amount={p['tokens']}")["outAmount"])
        except Exception as e:
            log(f"[err] prix indisponible {mint[:6]}: {e}")
            continue
        p["value"], p["peak"] = value, max(p.get("peak", 0), value)
        ratio = value / p["sol_in"]
        if ratio >= cfg["take_profit"]:
            sell(state, cfg, mint, f"take_profit x{ratio:.2f}")
        elif ratio <= cfg["stop_loss"]:
            sell(state, cfg, mint, f"stop_loss x{ratio:.2f}")
        elif time.time() - p["opened"] > cfg["max_hold_min"] * 60:
            sell(state, cfg, mint, f"timeout x{ratio:.2f}")


def poll_wallet(state, cfg, wallet):
    last = state["last_sig"].get(wallet)
    sigs = rpc("getSignaturesForAddress", wallet, {"limit": 20, **({"until": last} if last else {})})
    if not sigs:
        return
    state["last_sig"][wallet] = sigs[0]["signature"]
    if not last:  # premier passage : on ne copie pas l'historique
        return
    for s in reversed(sigs):
        if s.get("err"):
            continue
        tx = rpc("getTransaction", s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0})
        for side, mint in parse_swaps(tx, wallet):
            try:
                if side == "buy":
                    decision = buy(state, cfg, wallet, mint)
                elif mint in state["positions"] and state["positions"][mint]["wallet"] == wallet:
                    sell(state, cfg, mint, "leader_sold")
                    decision = "vendu"
                else:
                    decision = "pas de position"
            except Exception as e:
                log(f"[err] {side} {mint[:6]}: {e}")
                decision = f"erreur : {e}"
            EVENTS.appendleft({"at": time.time(), "wallet": wallet, "side": side, "mint": mint, "symbol": symbol(mint),
                               "sig": s["signature"], "decision": decision})


# ---------- tableau de bord ----------

def publish(state, cfg, sol_usd):
    """Fige l'état pour la page. Jamais de secret ici (clé privée, URL RPC qui peut contenir une clé)."""
    global snapshot
    wallets = []
    for w in cfg["wallets"]:
        n, pnl, wr = wallet_stats(state["trades"], w)
        sig = next((e for e in EVENTS if e["wallet"] == w), None)
        wallets.append({"wallet": w, "trades": n, "pnl": pnl / 1e9, "winrate": wr, "on": wallet_allowed(state["trades"], w, cfg),
                        "open": sum(p["wallet"] == w for p in state["positions"].values()), "lastSignal": sig and sig["at"]})
    snapshot = json.dumps({
        "now": time.time(), "startedAt": STARTED, "mode": "live" if LIVE else "paper", "solUsd": sol_usd, "rules": cfg,
        "wallets": wallets, "events": list(EVENTS), "logs": list(LOGS),
        "open": [{"mint": m, "symbol": p.get("symbol", ""), "wallet": p["wallet"], "cost": p["sol_in"] / 1e9,
                  "value": p.get("value", p["sol_in"]) / 1e9, "peak": p.get("peak", p["sol_in"]) / 1e9, "opened": p["opened"]}
                 for m, p in state["positions"].items()],
        "closed": [{**t, "cost": t["sol_in"] / 1e9, "proceeds": t["sol_out"] / 1e9} for t in reversed(state["trades"][-200:])],
    })


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/state":
            body, ctype = snapshot.encode(), "application/json"
        else:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html"), "rb") as f:
                body, ctype = f.read(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # pas de ligne par requête dans le terminal
        pass


def start_dashboard():
    if not DASHBOARD_PORT:
        return
    try:  # 127.0.0.1 seulement : sur un VPS, passer par ssh -L
        server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), Page)
    except OSError as e:
        return log(f"[err] tableau de bord indisponible : {e}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"tableau de bord : http://localhost:{DASHBOARD_PORT}")


# ---------- état & boucle ----------

def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {'[LIVE]' if LIVE else '[PAPER]'} {msg}"
    LOGS.append(line)
    print(line, flush=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"positions": {}, "trades": [], "last_sig": {}}


def save_state(state):
    with open(STATE_FILE + ".tmp", "w") as f:
        json.dump(state, f, indent=1)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)  # écriture atomique : pas d'état corrompu si crash


def report(state, cfg):
    for w in cfg["wallets"]:
        n, pnl, wr = wallet_stats(state["trades"], w)
        flag = "OK " if wallet_allowed(state["trades"], w, cfg) else "OFF"
        print(f"{flag} {w}  trades={n}  winrate={wr:.0%}  pnl={pnl / 1e9:+.4f} SOL")
    print(f"positions ouvertes: {len(state['positions'])}")


def main():
    with open("config.json") as f:
        cfg = json.load(f)
    state = load_state()
    if sys.argv[1:] == ["report"]:
        return report(state, cfg)
    if LIVE and not os.getenv("PRIVATE_KEY"):
        sys.exit("LIVE=1 exige PRIVATE_KEY (clé base58 d'un wallet dédié au bot)")
    start_dashboard()
    log(f"{len(cfg['wallets'])} wallets suivis, {cfg['buy_sol']} SOL par trade")
    sol_usd, sol_usd_at = None, 0
    while True:
        if time.time() - sol_usd_at > 60:
            sol_usd, sol_usd_at = token_info(SOL).get("usdPrice") or sol_usd, time.time()
        for w in cfg["wallets"]:
            try:
                poll_wallet(state, cfg, w)
            except Exception as e:
                log(f"[err] poll {w[:6]}: {e}")
            publish(state, cfg, sol_usd)
        check_exits(state, cfg)
        save_state(state)
        publish(state, cfg, sol_usd)
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
