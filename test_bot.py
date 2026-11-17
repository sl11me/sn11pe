import unittest
from bot import SOL, parse_swaps, wallet_allowed

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


if __name__ == "__main__":
    unittest.main()
