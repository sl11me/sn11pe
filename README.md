# sn11pe

Bot de copy-trading de wallets memecoins sur Solana. Il surveille des wallets « leaders »,
copie leurs achats via Jupiter (Swap API V2) et sort quand le leader vend, ou sur take-profit / stop-loss / timeout.

## Démarrage (paper trading, aucun vrai ordre)

```bash
cp config.example.json config.json   # mettre les adresses des wallets à copier
python3 bot.py                        # boucle de copie simulée (prix réels Jupiter)
python3 bot.py report                 # score de chaque wallet
python3 -m unittest                   # tests
```

Variables d'env optionnelles : `RPC_URL` (un RPC privé type Helius est fortement conseillé, le public est lent et limité),
`JUP_API_KEY` (https://developers.jup.ag/portal).

## Mode réel

```bash
pip install solders
LIVE=1 PRIVATE_KEY=<clé base58 d'un wallet DÉDIÉ> python3 bot.py
```

N'utilise jamais ton wallet principal : un wallet dédié avec juste la somme que tu acceptes de perdre.

## Auto-apprentissage

Chaque trade clôturé est enregistré dans `state.json`. Après `min_trades_to_judge` trades copiés d'un wallet,
il est désactivé si son PnL (pour nous, frais et slippage compris) est négatif ou son winrate < `min_winrate`.
`python3 bot.py report` montre le classement.

## Config (`config.json`)

| Clé | Rôle |
|---|---|
| `buy_sol` | taille de chaque copie en SOL |
| `max_positions` | positions ouvertes max |
| `slippage_bps` | slippage max (1500 = 15 %, les memecoins bougent vite) |
| `take_profit` / `stop_loss` | multiplicateurs de sortie (2.0 = x2, 0.6 = -40 %) |
| `max_hold_min` | durée max d'une position |
| `poll_seconds` | fréquence de surveillance |
