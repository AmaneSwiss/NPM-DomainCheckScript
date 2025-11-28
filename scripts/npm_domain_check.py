#!/usr/bin/env python3

import subprocess
import sys
import socket
import json
import os

# Your 'Nginx Proxy Manager' container name here
CONTAINER_NAME = "npm"

# Try to import MySQL driver
try:
    import mysql.connector
except ImportError:
    print("The package 'python3-mysql.connector' is required but not installed.")
    print("Please install it using your package manager, e.g., 'apt install python3-mysql.connector'")
    sys.exit(1)

# VARIABLES
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = os.path.splitext(os.path.basename(__file__))[0]
CONFIG_PATH = os.path.join(SCRIPT_DIR, SCRIPT_NAME + ".json")

def run_command(cmd):
    # Runs a shell command and returns stdout, stderr, returncode.
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def container_exists(name):
    # Checks if a Docker container with the specified name exists.
    stdout, stderr, rc = run_command(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if rc != 0:
        print(f"Error in querying Docker containers: {stderr}")
        sys.exit(1)
    return name in stdout.splitlines()


def get_env_from_container(name):
    # Reads environment variables from inside the container.
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


def resolve_ip(domain):
    # Resolves a domain to IPv4 address. Returns None on failure.
    try:
        return socket.gethostbyname(domain)
    except Exception:
        return None


def update_ip_in_container(container, old_ip, new_ip):
    # Updates IP inside proxy_host config files.
    if not old_ip or not new_ip:
        return

    cmd = [
        "docker", "exec", container,
        "sh", "-c",
        f"sed -i 's/{old_ip}/{new_ip}/g' /data/nginx/proxy_host/*"
    ]
    _, stderr, rc = run_command(cmd)

    if rc != 0:
        print(f"Error running sed in container: {stderr}")
    else:
        print(f"sed executed in container: {old_ip} → {new_ip}")


def reload_nginx(container):
    # Reloads nginx inside container.
    _, stderr, rc = run_command(["docker", "exec", container, "nginx", "-s", "reload"])
    if rc == 0:
        print("Nginx successfully reloaded.")
    else:
        print(f"Error reloading Nginx: {stderr}")


# JSON CONFIG HANDLING
def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except:
        return {}


def save_config(data):
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def main():
    # STEP 1: verify container exists
    if not container_exists(CONTAINER_NAME):
        print(f"Container '{CONTAINER_NAME}' does not exist.")
        sys.exit(0)

    # STEP 2: load JSON configuration
    domain_map = load_config()

    # STEP 3: read env vars
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

    # STEP 4: Try to connect to the database
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
        # STEP 5: Ensure column "domain" exists
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'access_list_client'
            AND COLUMN_NAME = 'domain'
            """, (db_name,)
            )

        # select key from dict result
        result = cursor.fetchone()
        column_exists = list(result.values())[0] == 1

        if not column_exists:
            print("Column 'domain' is missing, creating it...")
            try:
                cursor.execute("ALTER TABLE access_list_client ADD COLUMN domain VARCHAR(255)")
                conn.commit()
                print("Column 'domain' successfully created.")
            except mysql.connector.Error as err:
                print(f"Error creating column 'domain': {err}")
                sys.exit(1)


        # STEP 6: read data
        cursor.execute("SELECT id, domain, address FROM access_list_client")
        rows = cursor.fetchall()

        # separate domain and non-domain rows
        table_by_ip = {}
        for row in rows:
            ip = None
            if row["address"]:
                ip = row["address"].split("/")[0]
            table_by_ip[ip] = row

        nginx_reload_needed = False
        updated_count = 0

        # STEP 7: RESTORE missing domains from JSON
        for ip, domain in list(domain_map.items()):
            if ip not in table_by_ip:
                del domain_map[ip]
                continue

            db_row = table_by_ip[ip]
            if db_row["domain"] != domain:
                cursor.execute(
                    "UPDATE access_list_client SET domain=%s WHERE id=%s",
                    (domain, db_row["id"])
                )
                print(f"Domain for IP {ip} restored: {domain}")
                updated_count += 1

        # STEP 8: handle normal domain - resolve - update - record mapping
        for row in rows:
            row_id = row["id"]
            domain = row["domain"]
            address = row["address"]

            ip_old = None
            if address:
                ip_old = address.split("/")[0]

            if domain:
                ip_new = resolve_ip(domain)
                if not ip_new:
                    continue

                new_address = f"{ip_new}/32"

                if ip_new != ip_old:
                    cursor.execute(
                        "UPDATE access_list_client SET address=%s WHERE id=%s",
                        (new_address, row_id)
                    )
                    print(f"IP updated for {domain}: {ip_old} → {ip_new}")

                    update_ip_in_container(CONTAINER_NAME, ip_old, ip_new)
                    nginx_reload_needed = True
                    updated_count += 1

                domain_map[ip_new] = domain
                if ip_old in domain_map and ip_old != ip_new:
                    del domain_map[ip_old]

        # STEP 9: write JSON
        save_config(domain_map)

        # STEP 10: commit db
        conn.commit()

        if nginx_reload_needed:
            reload_nginx(CONTAINER_NAME)

        if updated_count > 0:
            print(f"Total changes: {updated_count}")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
