import unittest
from bot import SOL, is_successor_transfer, parse_swaps, parse_transfers, wallet_allowed

W, MEME = "Leader111", "Meme111"
CFG = {"min_trades_to_judge": 3, "min_winrate": 0.4}


def tx(sol_pre, sol_post, tok_pre, tok_post, err=None):
    bal = lambda amt, mint=MEME: [{"owner": W, "mint": mint, "uiTokenAmount": {"amount": str(amt)}}] if amt is not None else []
    return {"meta": {"err": err, "preBalances": [sol_pre], "postBalances": [sol_post],
                     "preTokenBalances": bal(tok_pre), "postTokenBalances": bal(tok_post)},
            "transaction": {"message": {"accountKeys": [{"pubkey": W}]}}}


class T(unittest.TestCase):
    def test_buy(self):
        self.assertEqual(parse_swaps(tx(10, 5, None, 1000), W), [("buy", MEME)])

    def test_sell(self):
        self.assertEqual(parse_swaps(tx(5, 10, 1000, 0), W), [("sell", MEME)])

    def test_failed_tx_ignored(self):
        self.assertEqual(parse_swaps(tx(10, 5, None, 1000, err="x"), W), [])

    def test_wsol_counts_as_sol(self):
        t = tx(10, 10, None, 1000)  # SOL natif inchangé mais wSOL dépensé
        t["meta"]["preTokenBalances"].append({"owner": W, "mint": SOL, "uiTokenAmount": {"amount": "500"}})
        self.assertEqual(parse_swaps(t, W), [("buy", MEME)])

    def test_learning_disables_losers(self):
        lose = [{"wallet": W, "sol_in": 10, "sol_out": 5}] * 3
        self.assertTrue(wallet_allowed(lose[:2], W, CFG))   # pas encore assez d'historique
        self.assertFalse(wallet_allowed(lose, W, CFG))      # perdant -> coupé
        win = [{"wallet": W, "sol_in": 10, "sol_out": 30}] * 3
        self.assertTrue(wallet_allowed(win, W, CFG))

    def test_transfers(self):
        ix = lambda src, dst, lam: {"program": "system", "parsed": {"type": "transfer", "info": {"source": src, "destination": dst, "lamports": lam}}}
        t = tx(10_000_000_000, 1_000_000_000, None, None)
        t["transaction"]["message"]["instructions"] = [ix(W, "New111", 6_000_000_000), ix("Other", W, 5)]
        t["meta"]["innerInstructions"] = [{"instructions": [ix(W, "New111", 3_000_000_000)]}]
        self.assertEqual(parse_transfers(t, W), [("New111", 9_000_000_000, 10_000_000_000)])

    def test_successor_threshold(self):
        cfg = {"successor_min_sol": 0.5, "successor_min_share": 0.5}
        self.assertTrue(is_successor_transfer(9e9, 10e9, cfg))   # vide 90 % du wallet
        self.assertFalse(is_successor_transfer(1e9, 10e9, cfg))  # simple paiement
        self.assertFalse(is_successor_transfer(1e8, 1e8, cfg))   # trop petit pour compter


if __name__ == "__main__":
    unittest.main()
