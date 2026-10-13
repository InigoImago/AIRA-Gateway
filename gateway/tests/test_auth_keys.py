from aira_gateway.auth import keys


def test_generate_roundtrip() -> None:
    full, prefix, key_hash = keys.generate_api_key()
    assert full.startswith("aira_")
    assert keys.parse_prefix(full) == prefix
    assert keys.hash_api_key(full) == key_hash
    assert keys.verify_hash(full, key_hash) is True


def test_verify_hash_rejects_wrong_key() -> None:
    _full, _prefix, key_hash = keys.generate_api_key()
    assert keys.verify_hash("aira_dead_beef", key_hash) is False


def test_parse_prefix_rejects_non_keys() -> None:
    assert keys.parse_prefix("notakey") is None
    assert keys.parse_prefix("aira_onlyone") is None
    assert keys.parse_prefix("other_a_b") is None


def test_is_aira_key() -> None:
    assert keys.is_aira_key("aira_a_b") is True
    assert keys.is_aira_key("eyJhbGciOiJ.fake.jwt") is False


def test_demo_key_is_valid_shape() -> None:
    assert keys.is_aira_key(keys.DEMO_API_KEY)
    assert keys.parse_prefix(keys.DEMO_API_KEY) == "demo0000"


def test_a_prefix_is_only_what_a_key_is_made_of() -> None:
    """The prefix is the caller's own bytes until it is looked up, and Postgres refuses a NUL in a
    text parameter with an error rather than an empty answer — a 500 for one unauthenticated
    request. Lowercase letters and digits, as long as the column, is every prefix ever issued."""
    full, prefix, _hash = keys.generate_api_key()
    assert keys.parse_prefix(full) == prefix
    assert keys.parse_prefix(keys.DEMO_API_KEY) == keys.DEMO_KEY_PREFIX
    for refused in ("ab\x00cd", "äbcd", "AB CD", "ab%00cd", "a" * 33, "ABCD1234"):
        assert keys.parse_prefix(f"aira_{refused}_secret") is None, repr(refused)
    assert keys.parse_prefix(f"aira_{'a' * 32}_secret") == "a" * 32
