import os

import requests
from dotenv import load_dotenv

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import MarketOrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY
except ModuleNotFoundError as exc:
    if exc.name == "py_clob_client":
        raise SystemExit(
            "Missing dependency 'py_clob_client'.\n"
            "Install with:\n"
            "  python3 -m pip install -r requirements-polymarket.txt"
        ) from exc
    raise

load_dotenv()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value)


#####################
### CONFIGURATION ###
#####################
TARGET_ADDRESS = os.getenv("POLYMARKET_TARGET_ADDRESS", "0x63ce342161250d705dc0b16df89036c8e5f9ba9a")

FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "0x11c6a04b48cca2d6435ca33421d0d73a74a83d41")
PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
SIGNATURE_TYPE = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))    # 0=EOA (Wallets), 1=Email/Magic, 2=Browser proxy

BET_AMOUNT = env_float("POLYMARKET_BET_AMOUNT", 1.0)            # Amount in $ to spend on each copied bet

DRY_RUN = env_bool("POLYMARKET_DRY_RUN", True)              # True = preview only, False = execute bets
FILTER_BITCOIN_UP_DOWN = True
BITCOIN_UP_DOWN_PREFIX = "Bitcoin Up or Down"
SHOW_TARGET_POSITION_BETS = True
TARGET_POSITION_BETS_LIMIT = 100


############
### APIs ###
############
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
PROFILE_API = "https://gamma-api.polymarket.com"


def get_profile_name(wallet_address: str) -> str:
    response = requests.get(f"{PROFILE_API}/public-profile", params={"address": wallet_address})
    response.raise_for_status()
    profile = response.json()
    return profile.get("name") or profile.get("pseudonym") or wallet_address[:10] + "..."


def get_positions(wallet_address: str) -> list:
    response = requests.get(
        f"{DATA_API}/positions",
        params={"user": wallet_address, "sizeThreshold": 0}
    )
    response.raise_for_status()
    return response.json()


def get_latest_bet(wallet_address: str) -> dict | None:
    response = requests.get(
        f"{DATA_API}/activity",
        params={"user": wallet_address, "limit": 20}
    )
    response.raise_for_status()

    for activity in response.json():
        is_buy_trade = activity.get("type") == "TRADE" and activity.get("side") == "BUY"
        if not is_buy_trade:
            continue

        if FILTER_BITCOIN_UP_DOWN:
            title = activity.get("title", "")
            if not title.startswith(BITCOIN_UP_DOWN_PREFIX):
                continue

        return activity
    return None


def get_target_bets_for_position(
    wallet_address: str,
    condition_id: str,
    outcome_index: int,
    limit: int = TARGET_POSITION_BETS_LIMIT,
) -> list:
    response = requests.get(
        f"{DATA_API}/activity",
        params={"user": wallet_address, "limit": limit},
    )
    response.raise_for_status()

    same_position_bets = []
    for activity in response.json():
        is_trade = activity.get("type") == "TRADE"
        same_condition = activity.get("conditionId") == condition_id
        same_outcome = activity.get("outcomeIndex") == outcome_index
        if is_trade and same_condition and same_outcome:
            same_position_bets.append(activity)
    return same_position_bets


def already_has_position(my_positions: list, conditionId: str, outcomeIndex: int) -> bool:
    key = lambda c, o: f"{c}_{o}"
    target_key = key(conditionId, outcomeIndex)
    my_keys = {key(p["conditionId"], p["outcomeIndex"]) for p in my_positions}
    return target_key in my_keys


def get_clob_client():
    client = ClobClient(
        CLOB_API,
        key=PRIVATE_KEY,
        chain_id=137,
        signature_type=SIGNATURE_TYPE,
        funder=FUNDER_ADDRESS
    )
    creds = client.derive_api_key()
    client.set_api_creds(creds)
    return client


def place_bet(client, token_id: str, amount: float):
    order = MarketOrderArgs(
        token_id=token_id,
        amount=amount,
        side=BUY,
        order_type=OrderType.FOK
    )
    signed_order = client.create_market_order(order)
    client.post_order(signed_order, OrderType.FOK)


############
### MAIN ###
############
def main():
    if not PRIVATE_KEY:
        raise SystemExit(
            "Missing POLYMARKET_PRIVATE_KEY environment variable.\n"
            "Set it in a .env file or export it in your shell.\n"
            "Example .env line:\n"
            "  POLYMARKET_PRIVATE_KEY=0x..."
        )

    target_name = get_profile_name(TARGET_ADDRESS)

    print("\n" + "=" * 60)
    print(f"  Copying: {target_name}")
    print(f"  Bet amount: ${BET_AMOUNT}")
    print(f"  Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print("=" * 60 + "\n")

    print("[1] Fetching target's latest bet...")
    latest = get_latest_bet(TARGET_ADDRESS)
    if not latest:
        print("    No recent bets found. Nothing to copy.")
        return

    title = latest["title"][:50]
    outcome = latest["outcome"]
    target_size = latest["size"]
    price = latest["price"]

    print(f"    Found: {title}")
    print(f"    Position: {target_size:.1f} {outcome} @ {price * 100:.1f}¢")


    if SHOW_TARGET_POSITION_BETS:
        print("\n[2] Target bets for this same position:")
        target_position_bets = get_target_bets_for_position(
            TARGET_ADDRESS,
            latest["conditionId"],
            latest["outcomeIndex"],
        )
        if target_position_bets:
            for idx, bet in enumerate(target_position_bets, start=1):
                side = bet.get("side", "?")
                size = float(bet.get("size", 0))
                bet_price = float(bet.get("price", 0))
                outcome_label = bet.get("outcome", "?")
                print(f"    {idx:>2}. {side:<4} {size:.2f} {outcome_label} @ {bet_price * 100:.1f}c")
        else:
            print("    No recent bets found for this exact position.")

    print("\n[3] Checking your positions...")
    my_positions = get_positions(FUNDER_ADDRESS)
    if my_positions:
        print(f"    Your open positions:")
        for pos in my_positions:
            print(f"      - {pos['title'][:40]}: {pos['outcome']}")
    else:
        print("    You have no open positions")

    if already_has_position(my_positions, latest["conditionId"], latest["outcomeIndex"]):
        print(f"    Already in this market. Nothing to do!")
        return

    print("    Not in this market yet. Proceeding...")

    print("\n[4] Placing bet...")
    client = get_clob_client()
    if DRY_RUN:
        print(f"    DRY RUN - would buy ${BET_AMOUNT:.2f} of {outcome}")
    else:
        place_bet(client, latest["asset"], BET_AMOUNT)
        print(f"    Done! Bought ${BET_AMOUNT:.2f} of {outcome}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"\nPolymarket API Error: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"\nError: {e}")
