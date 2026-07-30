"""Minimal static-word ABI helpers used at protocol boundaries."""


class ABIDecodingError(ValueError):
    pass


def decode_words(data: object, expected: int) -> tuple[int, ...]:
    value = str(data)
    if not value.startswith("0x"):
        raise ABIDecodingError("ABI data must start with 0x")
    payload = value[2:]
    if len(payload) != expected * 64:
        raise ABIDecodingError(f"Expected {expected} ABI words, received {len(payload) // 64}")
    try:
        return tuple(int(payload[index : index + 64], 16) for index in range(0, len(payload), 64))
    except ValueError as error:
        raise ABIDecodingError("ABI data contains non-hexadecimal characters") from error


def decode_topic_address(topic: object) -> str:
    value = str(topic).lower()
    if not value.startswith("0x") or len(value) != 66:
        raise ABIDecodingError("Indexed address topic must be 32 bytes")
    return f"0x{value[-40:]}"
