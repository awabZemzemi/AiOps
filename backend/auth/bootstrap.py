"""Create the first account only, using a hidden password prompt."""

import argparse
from getpass import GetPassWarning, getpass
import os
from pathlib import Path
import sys
import warnings

from dotenv import load_dotenv
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .accounts import lock_accounts
from .config import database_url
from .database import check_schema, make_engine, make_sessions
from .models import User
from .schemas import UserCreate
from .security import hash_password


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bootstrap the first AIOps administrator")
    parser.add_argument("--username", required=True)
    parser.add_argument("--namespace", action="append", default=[], dest="namespaces")
    args = parser.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    os.umask(0o077)
    engine = make_engine(database_url())
    try:
        check_schema(engine)
        with make_sessions(engine)() as session:
            if session.scalar(select(User.id).limit(1)):
                raise ValueError("Bootstrap requires an empty users table; use the Admin API.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("Admin password: ")
            confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise ValueError("Passwords do not match.")
        payload = UserCreate(username=args.username, password=password, role="admin",
                             allowed_namespaces=args.namespaces)
        encoded = hash_password(payload.password.get_secret_value())
        with make_sessions(engine).begin() as session:
            lock_accounts(session)
            if session.scalar(select(User.id).limit(1)):
                raise ValueError("Bootstrap requires an empty users table; use the Admin API.")
            session.add(User(**payload.model_dump(exclude={"password"}), password_hash=encoded))
        print("First administrator created.")
        return 0
    except ValidationError:
        print("Invalid username, namespace, or password length; no account created.", file=sys.stderr)
        return 1
    except (GetPassWarning, EOFError, KeyboardInterrupt):
        print("A hidden terminal password prompt is required; bootstrap cancelled.", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Database operation failed; check configuration and migrations.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
