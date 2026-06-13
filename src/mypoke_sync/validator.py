import logging

logger = logging.getLogger("validator")


def validate_card_data(data: dict) -> bool:
    """
    Validates core fields for a Pokémon card record before syncing.
    """
    required_fields = ["id", "name", "set_id"]
    for field in required_fields:
        if not data.get(field):
            logger.error(f"Validation failed: Missing required field '{field}' in card {data.get('id', 'Unknown')}")
            return False

    # Check numeric types if present
    if "dex_id" in data and data["dex_id"] is not None:
        if not isinstance(data["dex_id"], int):
            logger.error(f"Validation failed: 'dex_id' must be an integer for card {data['id']}")
            return False

    return True


def validate_price_data(data: dict) -> bool:
    """
    Validates card pricing data.
    """
    if not data.get("card_id"):
        logger.error("Validation failed: Price record missing 'card_id'")
        return False

    # All price fields should be numeric or None
    price_fields = ["market", "low", "mid", "high", "direct", "avg", "trend"]
    for field in price_fields:
        val = data.get(field)
        if val is not None and not isinstance(val, (int, float)):
            logger.error(
                f"Validation failed: Price field '{field}' must be numeric in price record for {data['card_id']}"
            )
            return False

    return True


def validate_set_data(data: dict) -> bool:
    """
    Validates set/expansion data.
    """
    required_fields = ["id", "name"]
    for field in required_fields:
        if not data.get(field):
            logger.error(f"Validation failed: Missing required field '{field}' in set {data.get('id', 'Unknown')}")
            return False
    return True
