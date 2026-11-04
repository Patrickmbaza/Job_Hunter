"""Create an application password verifier without writing plaintext credentials."""

import getpass

from app.auth import hash_password


def main() -> None:
    first = getpass.getpass("Choose a local dashboard password (14+ characters): ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("Passwords did not match")
    try:
        encoded = hash_password(first)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print("Add this verifier to your ignored project .env file as APP_PASSWORD_HASH=<value>:")
    print(encoded)
    print("The plaintext password was not written to disk.")


if __name__ == "__main__":
    main()
