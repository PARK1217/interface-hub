from app.core.security import decrypt_secret, encrypt_secret


def test_aes_gcm_round_trip():
    plain = "super-secret-api-key-12345"
    blob = encrypt_secret(plain)
    assert blob != plain
    assert decrypt_secret(blob) == plain


def test_empty_secret():
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""
