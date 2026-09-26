"""Create an admin user, or make an existing user admin.

    python -m app.scripts.create_admin admin@example.com "Admin" 'password123'
"""
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import User


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    email, name, password = sys.argv[1].lower(), sys.argv[2], sys.argv[3]

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            user.is_admin = True
            print(f"{email} is now an admin")
        else:
            db.add(User(email=email, full_name=name, hashed_password=hash_password(password), is_admin=True))
            print(f"created admin {email}")
        db.commit()


if __name__ == "__main__":
    main()
