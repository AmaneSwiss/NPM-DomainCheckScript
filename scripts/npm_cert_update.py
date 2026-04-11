#!/usr/bin/env python3

import subprocess
import sys
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Your 'Nginx Proxy Manager' container name here
CONTAINER_NAME = "npm"

# Database certificate row ID
CERTIFICATE_ID = 1
# Certificate file paths
CERT_PATH = f"/opt/npm/data/custom_ssl/npm-{CERTIFICATE_ID}/cert.pem"
FULLCHAIN_PATH = f"/opt/npm/data/custom_ssl/npm-{CERTIFICATE_ID}/fullchain.pem"
PRIVKEY_PATH = f"/opt/npm/data/custom_ssl/npm-{CERTIFICATE_ID}/privkey.pem"

# Get system timezone dynamically
def get_system_timezone():
    # Try reading /etc/timezone (Debian/Ubuntu)
    try:
        with open('/etc/timezone', 'r') as f:
            tz_name = f.read().strip()
            if tz_name:
                return ZoneInfo(tz_name)
    except (FileNotFoundError, Exception):
        pass
    
    # Try resolving /etc/localtime symlink
    try:
        localtime_path = os.path.realpath('/etc/localtime')
        # Extract timezone from path like /usr/share/zoneinfo/Europe/Zurich
        if '/zoneinfo/' in localtime_path:
            tz_name = localtime_path.split('/zoneinfo/')[-1]
            return ZoneInfo(tz_name)
    except Exception:
        pass
    
    # Fallback to UTC
    return ZoneInfo('UTC')

# Timezone
TIMEZONE = get_system_timezone()

# Try to import MySQL driver
try:
    import mysql.connector
except ImportError:
    print("The package 'python3-mysql.connector' is required but not installed.")
    print("Please install it using your package manager, e.g., 'apt install python3-mysql.connector'")
    sys.exit(1)

# Try to import cryptography for certificate parsing
try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
except ImportError:
    print("The package 'python3-cryptography' is required but not installed.")
    print("Please install it using your package manager, e.g., 'apt install python3-cryptography' or 'pip install cryptography'")
    sys.exit(1)

# Counts running instances of this script
SCRIPT_NAME = os.path.basename(sys.argv[0])
def count_instances():
    pid_self = str(os.getpid())
    result = subprocess.run(
        ["pgrep", "-f", SCRIPT_NAME],
        capture_output=True,
        text=True
    )
    pids = [p for p in result.stdout.split() if p != pid_self]
    return len(pids)

# Command execution helper
def run_command(cmd):
    # Runs a shell command and returns stdout, stderr, returncode.
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
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Error in querying Docker containers: {stderr}")
        sys.exit(1)
    return name in stdout.splitlines()

# Reads environment variables from inside the container.
def get_env_from_container(name):
    stdout, stderr, rc = run_command(["docker", "exec", name, "env"])
    if rc != 0:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Error reading environment variables from container '{name}': {stderr}")
        sys.exit(1)

    env = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env

# Reads certificate file and returns content
def read_cert_file(filepath):
    if not os.path.exists(filepath):
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Certificate file not found: {filepath}")
        return None
    try:
        with open(filepath, "r") as f:
            return f.read()
    except Exception as e:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Error reading file {filepath}: {e}")
        return None

# Extracts expiry date from certificate
def get_cert_expiry_date(cert_content):
    if not cert_content:
        return None
    try:
        # Parse the certificate
        cert = x509.load_pem_x509_certificate(cert_content.encode(), default_backend())
        # Get the expiry date (not_valid_after) and format it
        # Try not_valid_after_utc first (newer versions), fallback to not_valid_after
        try:
            expires = cert.not_valid_after_utc
        except AttributeError:
            expires = cert.not_valid_after
            # Older versions return naive datetime, assume UTC
            if expires.tzinfo is None:
                from datetime import timezone
                expires = expires.replace(tzinfo=timezone.utc)
        
        # Convert to the system timezone
        expires_local = expires.astimezone(TIMEZONE)
        # Format as MySQL datetime
        return expires_local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Error parsing certificate expiry date: {e}")
        return None

# Converts certificate content to JSON format (with \n for newlines, no extra spaces)
def cert_to_json_string(cert_content):
    if cert_content is None:
        return ""
    # Replace actual newlines with \n literal string
    return cert_content.replace("\n", "\\n").replace(" ", "")

# Reloads nginx inside container.
def reload_nginx(container):
    _, stderr, rc = run_command(["docker", "exec", container, "nginx", "-s", "reload"])
    if rc == 0:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Nginx successfully reloaded.")
    else:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Error reloading Nginx: {stderr}")


# Main function
def main():
    # STEP 1: check container existence
    if not container_exists(CONTAINER_NAME):
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Container '{CONTAINER_NAME}' does not exist.")
        sys.exit(0)

    # STEP 2: Read certificate files
    cert_content = read_cert_file(CERT_PATH)
    fullchain_content = read_cert_file(FULLCHAIN_PATH)
    privkey_content = read_cert_file(PRIVKEY_PATH)

    if cert_content is None or fullchain_content is None or privkey_content is None:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] One or more certificate files could not be read. Exiting.")
        sys.exit(1)

    # STEP 3: Build JSON structure
    cert_json = {
        "certificate": cert_content,
        "intermediate_certificate": fullchain_content,
        "certificate_key": privkey_content
    }
    
    # Convert to JSON string (compact format, no spaces after separators)
    new_meta = json.dumps(cert_json, separators=(',', ':'), ensure_ascii=False)
    
    # STEP 3.1: Extract certificate expiry date
    cert_expiry = get_cert_expiry_date(cert_content)
    if not cert_expiry:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Warning: Could not extract certificate expiry date.")

    # STEP 4: get env vars from container
    env = get_env_from_container(CONTAINER_NAME)

    # STEP 4.1: verify required env vars
    required_vars = [
        "DB_MYSQL_HOST",
        "DB_MYSQL_PORT",
        "DB_MYSQL_NAME",
        "DB_MYSQL_USER",
        "DB_MYSQL_PASSWORD",
    ]

    missing = [v for v in required_vars if v not in env]

    if missing:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Missing environment variables in container:")
        print("\n".join(missing))
        sys.exit(1)

    # STEP 4.2: extract env vars
    db_host = env["DB_MYSQL_HOST"]
    db_port = int(env["DB_MYSQL_PORT"])
    db_name = env["DB_MYSQL_NAME"]
    db_user = env["DB_MYSQL_USER"]
    db_password = env["DB_MYSQL_PASSWORD"]

    # STEP 5: Try to connect to the database
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
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] DB connection error: {err}")
        sys.exit(1)

    cursor = conn.cursor(dictionary=True)

    # STEP 6: Process the certificate table
    try:
        # STEP 6.1: Read current meta value from database
        cursor.execute(
            "SELECT id, meta FROM certificate WHERE id = %s",
            (CERTIFICATE_ID,)
        )
        row = cursor.fetchone()

        if not row:
            print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Certificate with ID {CERTIFICATE_ID} not found in database.")
            sys.exit(1)

        current_meta = row["meta"]

        # STEP 6.2: Compare current meta with new meta by parsing JSON
        try:
            current_cert_data = json.loads(current_meta) if current_meta else {}
        except json.JSONDecodeError:
            current_cert_data = {}
        
        # Compare the actual certificate contents, not the JSON strings
        cert_changed = (
            current_cert_data.get("certificate") != cert_content or
            current_cert_data.get("intermediate_certificate") != fullchain_content or
            current_cert_data.get("certificate_key") != privkey_content
        )
        
        if cert_changed:
            # Update the certificate
            now = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S.000")
            
            # Update with expires_on if available
            if cert_expiry:
                cursor.execute(
                    "UPDATE certificate SET meta = %s, modified_on = %s, expires_on = %s WHERE id = %s",
                    (new_meta, now, cert_expiry, CERTIFICATE_ID)
                )
            else:
                cursor.execute(
                    "UPDATE certificate SET meta = %s, modified_on = %s WHERE id = %s",
                    (new_meta, now, CERTIFICATE_ID)
                )
            conn.commit()
            
            print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Certificate updated in database (ID: {CERTIFICATE_ID})")
            print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Modified timestamp: {now}")
            if cert_expiry:
                print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] Certificate expires: {cert_expiry}")
            
            # Reload nginx
            reload_nginx(CONTAINER_NAME)

    # STEP 7: Cleanup
    finally:
        cursor.close()
        conn.close()

# Run main function
if __name__ == "__main__":
    count = count_instances()
    if count > 1:
        print(f"[{datetime.now(TIMEZONE):%Y-%m-%d %H:%M:%S}] SKIP: Skript {SCRIPT_NAME} läuft bereits ({count} Prozesse gefunden)")
        sys.exit(1)

    main()
