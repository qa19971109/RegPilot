from __future__ import annotations

import random
import secrets
import string


def random_password(length: int = 16) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    value = list(
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + secrets.choice("!@#$%")
        + "".join(secrets.choice(chars) for _ in range(max(0, length - 4)))
    )
    random.shuffle(value)
    return "".join(value)


def random_name() -> tuple[str, str]:
    first_names = [
        "James", "Robert", "John", "Michael", "David", "William", "Richard", "Thomas", "Daniel", "Matthew",
        "Mary", "Emma", "Olivia", "Sophia", "Emily", "Grace", "Lily", "Anna", "Chloe", "Nora",
    ]
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Wilson", "Moore",
        "Taylor", "Anderson", "Thomas", "Martin", "Lee", "Walker", "Hall", "Allen", "Young", "King",
    ]
    return random.choice(first_names), random.choice(last_names)


def random_birthdate() -> str:
    # Keep accounts clearly adult while avoiding a too-narrow repeated date distribution.
    return f"{random.randint(1985, 2004):04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
