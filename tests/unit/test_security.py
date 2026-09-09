import pytest

from libs.common.config import Settings
from libs.common.errors import UnauthorizedError, ValidationError
from libs.common.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

settings = Settings(jwt_secret="unit-test-secret-of-sufficient-length-32+", environment="test")


def test_password_round_trip():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_overlong_password_is_rejected_not_truncated():
    with pytest.raises(ValidationError):
        hash_password("a" * 100)
    assert not verify_password("a" * 100, hash_password("a" * 70))


def test_access_token_round_trip():
    token = create_access_token(subject="usr_1", settings=settings, email="a@b.c")
    payload = decode_token(token, settings=settings)
    assert payload["sub"] == "usr_1"
    assert payload["email"] == "a@b.c"


def test_refresh_token_is_not_accepted_as_access_token():
    token = create_refresh_token(subject="usr_1", settings=settings)
    with pytest.raises(UnauthorizedError):
        decode_token(token, settings=settings, expected_type="access")


def test_tampered_token_is_rejected():
    token = create_access_token(subject="usr_1", settings=settings)
    with pytest.raises(UnauthorizedError):
        decode_token(token + "x", settings=settings)


def test_expired_token_is_rejected():
    token = create_access_token(subject="usr_1", settings=settings, expires_minutes=-1)
    with pytest.raises(UnauthorizedError):
        decode_token(token, settings=settings)
