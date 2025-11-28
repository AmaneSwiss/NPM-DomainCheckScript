#!/usr/bin/env python3

import subprocess
import sys

# Your 'Nginx Proxy Manager' container name here
CONTAINER_NAME = "npm"

# Try to import MySQL driver
try:
    import mysql.connector
except ImportError:
    print("The package 'python3-mysql.connector' is required but not installed.")
    print("Please install it using your package manager, e.g., 'apt install python3-mysql.connector'")
    sys.exit(1)


def run_command(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def container_exists(name):
    stdout, stderr, rc = run_command(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if rc != 0:
        print(f"Error in querying Docker containers: {stderr}")
        sys.exit(1)
    return name in stdout.splitlines()


def get_env_from_container(name):
    stdout, stderr, rc = run_command(["docker", "exec", name, "env"])
    if rc != 0:
        print(f"Error reading environment variables from container '{name}': {stderr}")
        sys.exit(1)

    env = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def main():
    if not container_exists(CONTAINER_NAME):
        print(f"Container '{CONTAINER_NAME}' does not exist.")
        sys.exit(1)

    env = get_env_from_container(CONTAINER_NAME)

    required_vars = [
        "DB_MYSQL_HOST",
        "DB_MYSQL_PORT",
        "DB_MYSQL_NAME",
        "DB_MYSQL_USER",
        "DB_MYSQL_PASSWORD",
    ]

    missing = [v for v in required_vars if v not in env]
    if missing:
        print("Missing environment variables in container:")
        print("\n".join(missing))
        sys.exit(1)

    db_host = env["DB_MYSQL_HOST"]
    db_port = int(env["DB_MYSQL_PORT"])
    db_name = env["DB_MYSQL_NAME"]
    db_user = env["DB_MYSQL_USER"]
    db_password = env["DB_MYSQL_PASSWORD"]

    # Try to connect to the database
    try:
        conn = mysql.connector.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=db_name,
            ssl_disabled=True,
        )
    except mysql.connector.Error as err:
        print(f"DB connection error: {err}")
        sys.exit(1)

    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'access_list_client'
            AND COLUMN_NAME = 'domain'
            """,
            (db_name,),
        )

        exists = list(cursor.fetchone().values())[0] == 1

        if not exists:
            print("Column 'domain' does not exist, nothing to do.")
            return

        print("Removing column 'domain'...")

        try:
            cursor.execute("ALTER TABLE access_list_client DROP COLUMN domain")
            conn.commit()
            print("Column 'domain' successfully removed.")
        except mysql.connector.Error as err:
            print(f"Error removing column: {err}")
            sys.exit(1)

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
