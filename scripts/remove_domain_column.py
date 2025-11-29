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

# Command execution helper
def run_command(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode

# Checks if Docker container exists
def container_exists(name):
    stdout, stderr, rc = run_command(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if rc != 0:
        print(f"Error in querying Docker containers: {stderr}")
        sys.exit(1)
    return name in stdout.splitlines()

# Reads environment variables from inside the container.
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

# Main function
def main():
    # STEP 1: check container existence
    if not container_exists(CONTAINER_NAME):
        print(f"Container '{CONTAINER_NAME}' does not exist.")
        sys.exit(1)

    # STEP 2: get environment variables from container
    env = get_env_from_container(CONTAINER_NAME)

    # STEP 2.1: validate required env vars
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

    # STEP 2.2: extract env vars
    db_host = env["DB_MYSQL_HOST"]
    db_port = int(env["DB_MYSQL_PORT"])
    db_name = env["DB_MYSQL_NAME"]
    db_user = env["DB_MYSQL_USER"]
    db_password = env["DB_MYSQL_PASSWORD"]

    # STEP 3: Try to connect to the database
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

    cursor = conn.cursor()

    # STEP 4: Check if 'domain' column exists
    try:
        # STEP 4.1: Check column existence
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'access_list_client'
            AND COLUMN_NAME = 'domain'
            """, (db_name,)
        )

        exists = cursor.fetchone()[0] > 0

        # STEP 4.2: Skip if column does not exist
        if not exists:
            print("Column 'domain' does not exist, nothing to do.")
            return

        # STEP 4.3: Remove the 'domain' column
        print("Removing column 'domain'...")
        try:
            cursor.execute(
                """
                ALTER TABLE access_list_client
                DROP COLUMN domain
                """
            )
            conn.commit()
            print("Column 'domain' successfully removed.")
        except mysql.connector.Error as err:
            print(f"Error removing column: {err}")
            sys.exit(1)

    # STEP 5: Cleanup
    finally:
        cursor.close()
        conn.close()

# Run main function
if __name__ == "__main__":
    main()
