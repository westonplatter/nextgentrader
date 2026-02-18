"""Helpers for handling IBKR account identifiers safely."""


def mask_ibkr_account(account: str) -> str:
    """
    Mask an account string to avoid exposing the full identifier.

    Examples:
    - DU1234567 -> DU1*****
    - U9999999 -> U9*****
    """
    normalized = account.strip()
    if not normalized:
        return "***"

    first_digit_index = next(
        (idx for idx, char in enumerate(normalized) if char.isdigit()),
        -1,
    )
    if first_digit_index >= 0:
        visible_prefix = normalized[: first_digit_index + 1]
    else:
        visible_prefix = normalized[:1]

    hidden_count = max(1, len(normalized) - len(visible_prefix))
    return f"{visible_prefix}{'*' * hidden_count}"
