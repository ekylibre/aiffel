import uuid

import pytest

from aiffel.vault.crypto import (
    Keyring,
    SecretValue,
    VaultError,
    decrypt_secret,
    encrypt_secret,
    generate_key,
    new_wrapped_data_key,
    rewrap_data_key,
    secret_aad,
    unwrap_data_key,
)


@pytest.fixture
def keyring() -> Keyring:
    return Keyring.parse(generate_key())


def test_secret_value_is_masked() -> None:
    value = SecretValue("abc-token")
    assert "abc-token" not in repr(value)
    assert "abc-token" not in str(value)
    assert "abc-token" not in f"{value}"
    assert value.reveal() == "abc-token"


def test_keyring_parse_versions() -> None:
    k1, k2 = generate_key(), generate_key()
    keyring = Keyring.parse(f"1:{k1}, 2:{k2}")
    assert keyring.current == 2
    assert k1 not in repr(keyring)


@pytest.mark.parametrize("spec", ["", "1:", "x:" + "a" * 43, "1:dHJvcC1jb3VydA", "300:" + "a" * 43])
def test_keyring_rejects_invalid_specs(spec: str) -> None:
    with pytest.raises(VaultError):
        Keyring.parse(spec)


def test_data_key_roundtrip(keyring: Keyring) -> None:
    farm = uuid.uuid4()
    wrapped = new_wrapped_data_key(keyring, farm)
    assert len(unwrap_data_key(keyring, farm, wrapped)) == 32


def test_data_key_is_bound_to_its_farm(keyring: Keyring) -> None:
    wrapped = new_wrapped_data_key(keyring, uuid.uuid4())
    with pytest.raises(VaultError):
        unwrap_data_key(keyring, uuid.uuid4(), wrapped)


def test_data_key_needs_the_right_master_key(keyring: Keyring) -> None:
    farm = uuid.uuid4()
    wrapped = new_wrapped_data_key(keyring, farm)
    with pytest.raises(VaultError):
        unwrap_data_key(Keyring.parse(generate_key()), farm, wrapped)


def test_master_key_rotation(keyring: Keyring) -> None:
    farm = uuid.uuid4()
    wrapped_v1 = new_wrapped_data_key(keyring, farm)
    data_key = unwrap_data_key(keyring, farm, wrapped_v1)

    rotated = Keyring(keys={**keyring.keys, 2: bytes(32)}, current=2)
    wrapped_v2 = rewrap_data_key(rotated, farm, wrapped_v1)
    assert wrapped_v2[0] == 2
    assert unwrap_data_key(rotated, farm, wrapped_v2) == data_key

    only_v2 = Keyring(keys={2: bytes(32)}, current=2)
    assert unwrap_data_key(only_v2, farm, wrapped_v2) == data_key
    with pytest.raises(VaultError):
        unwrap_data_key(only_v2, farm, wrapped_v1)


def test_secret_is_bound_to_its_row(keyring: Keyring) -> None:
    farm, connection = uuid.uuid4(), uuid.uuid4()
    data_key = unwrap_data_key(keyring, farm, new_wrapped_data_key(keyring, farm))
    aad = secret_aad(farm, connection, "simple_token")
    ciphertext, nonce = encrypt_secret(data_key, aad, SecretValue("tok"))

    assert decrypt_secret(data_key, aad, ciphertext, nonce).reveal() == "tok"
    for other in (
        secret_aad(farm, uuid.uuid4(), "simple_token"),
        secret_aad(uuid.uuid4(), connection, "simple_token"),
        secret_aad(farm, connection, "password"),
    ):
        with pytest.raises(VaultError):
            decrypt_secret(data_key, other, ciphertext, nonce)


def test_tampered_secret_is_rejected(keyring: Keyring) -> None:
    farm, connection = uuid.uuid4(), uuid.uuid4()
    data_key = unwrap_data_key(keyring, farm, new_wrapped_data_key(keyring, farm))
    aad = secret_aad(farm, connection, "simple_token")
    ciphertext, nonce = encrypt_secret(data_key, aad, SecretValue("tok"))
    tampered = bytes([ciphertext[0] ^ 1]) + ciphertext[1:]
    with pytest.raises(VaultError):
        decrypt_secret(data_key, aad, tampered, nonce)


def test_global_encryption_roundtrip_and_purpose_binding(keyring: Keyring) -> None:
    from aiffel.vault.crypto import decrypt_global, encrypt_global

    blob = encrypt_global(keyring, "channel-id:telegram", "123456789")
    assert b"123456789" not in blob
    assert decrypt_global(keyring, "channel-id:telegram", blob) == "123456789"
    with pytest.raises(VaultError):
        decrypt_global(keyring, "channel-id:whatsapp", blob)
    with pytest.raises(VaultError):
        decrypt_global(Keyring.parse(generate_key()), "channel-id:telegram", blob)
