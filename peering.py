#!/usr/bin/env python3

import argparse
import getpass
import ipaddress
import logging
import os
import sys
from datetime import datetime

import requests
from jinja2 import Template
from netmiko import Netmiko

# -------------------------------------------------------------------
# Configuration Section
# -------------------------------------------------------------------
# Set this to your network ID in PeeringDB
OUR_NETWORK_IDS = ["12345"]  # Replace with your actual network ID!!!!
# Number of backup configs to keep before rotating out old ones
NUMBER_OF_BACKUPS = 5
# Directory to store backup configs before config deployment
BACKUP_DIR = "backups"

BACKUP_COMMANDS = {
    "juniper": "show configuration | display set",
    "cisco_xr": "show running-config",
    "cisco_xe": "show running-config",
    "arista_eos": "show running-config",
}

VERIFY_COMMANDS = {
    "juniper": ["show bgp summary | match {asn}"],
    "cisco_xr": ["show bgp sessions | i {asn}"],
    "cisco_xe": ["show bgp all summary | i {asn}"],
    "arista_eos": [
        "show ip bgp summary | grep {asn}",
        "show ipv6 bgp summary | grep {asn}"
    ]
}
TEMPLATES_DIR = "templates"
# -------------------------------------------------------------------
# End configuration section
# -------------------------------------------------------------------

API = "https://peeringdb.com/api"
API_TIMEOUT = 30
LOG_FORMAT = "%(asctime)s %(filename)s:%(lineno)d %(levelname)s %(message)s"
PROTO_LIST = ["ipaddr4", "ipaddr6"]
PROTO_TRANSLATE = {"IPv4": "ipaddr4", "IPv6": "ipaddr6"}


# Setup logging based on verbosity/debug flags
def setup_logging(verbose, debug):
    level = logging.WARNING
    if debug: level = logging.DEBUG
    elif verbose: level = logging.INFO
    logging.basicConfig(format=LOG_FORMAT, stream=sys.stderr, level=level)

# Fetch our ASN from PeeringDB using the provided network ID(s). This is used in the templates and for validation.
def fetch_our_asn():
    try:
        url = f"{API}/net/{OUR_NETWORK_IDS[0]}"
        data = requests.get(url, timeout=API_TIMEOUT).json()
        if data.get("data"):
            return data["data"][0]["asn"]
    except Exception as e:
        logging.warning(f"Could not fetch our ASN: {e}")
    return None

# Fetch peer network info and calculate max prefixes based on PeeringDB's info_prefixes4 and info_prefixes6.
def get_asn_data(peer_asn, networks):
    try:
        net_url = f"{API}/net?asn={peer_asn}"
        net_resp = requests.get(net_url, timeout=API_TIMEOUT).json()
        
        if not net_resp.get("data"):
            print(f"!!! Error: ASN {peer_asn} not found in PeeringDB !!!")
            sys.exit(1)
            
        net_info = net_resp["data"][0]
        net_name = net_info.get("name", f"AS{peer_asn}")

        # Retrieve info_prefixes from the API response
        raw_v4 = net_info.get("info_prefixes4")
        raw_v6 = net_info.get("info_prefixes6")

        # Convert to int, handling potential None or string types gracefully
        if raw_v4 is not None and raw_v4 != "":
            max_v4 = int(raw_v4)
        else:
            # Fallback to 100 if data is missing
            max_v4 = 100 

        if raw_v6 is not None and raw_v6 != "":
            max_v6 = int(raw_v6)
        else:
            max_v6 = 100

        ix_url = f"{API}/netixlan?asn={peer_asn}"
        ix_resp = requests.get(ix_url, timeout=API_TIMEOUT).json()
        peering_data = ix_resp.get("data", [])

        return net_name, peering_data, max_v4, max_v6
    except Exception as e:
        logging.critical(f"Error fetching Peer data: {e}")
        sys.exit(1)

# Fetch prefixes advertised by the IXes we peer at
def fetch_prefixes(networks):
    prefixes = {}
    for ix_id in networks.keys():
        prefixes[ix_id] = {proto: [] for proto in PROTO_LIST}
        try:
            url = f"{API}/ixpfx?ixlan_id={ix_id}"
            resp = requests.get(url, timeout=API_TIMEOUT).json()
            for net in resp.get("data", []):
                proto = PROTO_TRANSLATE.get(net["protocol"])
                if proto:
                    prefixes[ix_id][proto].append(ipaddress.ip_network(net["prefix"]))
        except Exception as e:
            logging.error(f"Failed to fetch prefixes for IX {ix_id}: {e}")
    return prefixes

# Validate the peer data against the prefixes advertised by the IX
def validate_peering_data(peering_data, prefixes):
    valid_peers = []
    for peer in peering_data:
        ix_id = str(peer["ix_id"])
        if ix_id not in prefixes: 
            continue
            
        is_valid = False
        for proto in PROTO_LIST:
            ip_str = peer.get(proto)
            if not ip_str: 
                continue
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                # Check if the peer IP falls within one of the prefixes advertised by the IX
                if any(ip_obj in subnet for subnet in prefixes[ix_id][proto]):
                    is_valid = True
            except ValueError: 
                continue
        if is_valid: 
            valid_peers.append(peer)
    return valid_peers

# Load the router configuration from routers.cfg, which should be in the format:
# hostname, device_type, ix_id, ix_name, our_ipv4, our_ipv6
def load_config():
    our_asn = fetch_our_asn()
    if not os.path.exists("routers.cfg"):
        print("!!! Error: routers.cfg not found !!!")
        sys.exit(1)

    networks = {}
    with open("routers.cfg") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            try:
                parts = [p.strip() for p in line.split(",")]
                hostname, dev_type, ix_id, ix_name, ipv4, ipv6 = parts
                device = {
                    "hostname": hostname, "device_type": dev_type,
                    "ix_name": ix_name, "our_ipv4": ipv4,
                    "our_ipv6": ipv6, "our_asn": our_asn,
                }
                networks.setdefault(ix_id, {"devices": []})
                networks[ix_id]["devices"].append(device)
            except (ValueError, IndexError):
                logging.warning(f"Invalid line in routers.cfg: {line}")
    return networks

# Render the configuration template for each peer, passing the max prefix limits for validation in the template
def render_template(peers, device, net_name, max_v4, max_v6):
    template_file = os.path.join(TEMPLATES_DIR, f"{device['device_type']}.j2")
    if not os.path.isfile(template_file):
        print(f"!!! Warning: Template {template_file} not found !!!")
        return ""

    with open(template_file) as f:
        template = Template(f.read())

    SEQUENCE = ["PRIMARY", "SECONDARY", "TERTIARY", "QUATERNARY", "QUINARY"]
    config = ""
    
    # Log for debugging/visibility
    logging.info(f"PeeringDB Limits for {net_name}: IPv4={max_v4}, IPv6={max_v6}")

    for i, peer in enumerate(peers):
        peer_tag = SEQUENCE[i] if i < len(SEQUENCE) else f"PEER_{i+1}"
        data = {
            "peer_ipv4": peer.get("ipaddr4"), 
            "peer_ipv6": peer.get("ipaddr6"),
            "peer_asn": peer["asn"], 
            "peer_name": net_name, 
            "peer_primary": peer_tag,
            "our_asn": device["our_asn"], 
            "ix_name": device["ix_name"],
            "our_ipv4": device["our_ipv4"], 
            "our_ipv6": device["our_ipv6"],
            "max_prefix_v4": max_v4, 
            "max_prefix_v6": max_v6,
        }
        config += template.render(data) + "\n"
    return config

# Connect to the router via SSH, backup the current config, check for existing peer IPs or ASNs to prevent overwriting, push the new config, and verify BGP status post-deployment.
def exec_ssh(router, username, commands, password, peer_asn, peers):
    try:
        conn = Netmiko(
            host=router["hostname"], username=username, password=password,
            device_type=router["device_type"], timeout=30
        )
        
        # 1. Backup current config
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_path = f"{BACKUP_DIR}/{router['hostname']}_{ts}.cfg"
        bak_cmd = BACKUP_COMMANDS.get(router["device_type"], "show running-config")
        
        running_config = conn.send_command(bak_cmd)
        with open(bak_path, "w") as f:
            f.write(running_config)
        print(f"Configuration backed up to: {bak_path}")

        # 2. Check for existing peer IPs in config to prevent overwriting
        existing_conflict = False
        peer_ips = [p.get("ipaddr4") for p in peers if p.get("ipaddr4")] + \
                   [p.get("ipaddr6") for p in peers if p.get("ipaddr6")]

        for ip in peer_ips:
            if ip and ip in running_config:
                print(f"\n!!! WARNING: Peer IP {ip} already exists on device {router['hostname']} !!!")
                existing_conflict = True
        
        # Look for the ASN specifically in a neighbor context to prevent false positives with local ASN
        if f"as {peer_asn}" in running_config.lower():
            print(f"!!! WARNING: Remote-AS {peer_asn} detected in config on {router['hostname']} !!!")
            existing_conflict = True

        if existing_conflict:
            print("!!! Exiting to prevent configuration overwrite. !!!")
            conn.disconnect()
            return

        # 3. Push Config
        config_list = [c.strip() for c in commands.split("\n") if c.strip()]
        if config_list:
            conn.send_config_set(config_list)
            if router["device_type"] == "juniper":
                conn.send_command("commit")
            else:
                conn.save_config()

        # 4. Verify BGP status post config
        v_cmds = VERIFY_COMMANDS.get(router["device_type"], ["show bgp summary"])
        print(f"\n--- Verification: {router['hostname']} ---")
        for v_cmd in v_cmds:
            formatted_cmd = v_cmd.format(asn=peer_asn)
            print(f"> {formatted_cmd}")
            print(conn.send_command(formatted_cmd))
        
        conn.disconnect()
    except Exception as e:
        print(f"!!! Deployment failed on {router['hostname']}: {e} !!!")

# Main execution flow: parse arguments, load config, fetch PeeringDB data, validate peers, render templates, and deploy to routers with safety checks. 
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ASN", type=int)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("-n", "--noop", action="store_true")
    parser.add_argument("-u", "--username")
    args = parser.parse_args()

    setup_logging(args.verbose, False)
    networks = load_config()
    prefixes = fetch_prefixes(networks)
    
    # Fetch peer data and max prefix limits
    net_name, peering_data, max_v4, max_v6 = get_asn_data(args.ASN, networks)
    valid_peers_all = validate_peering_data(peering_data, prefixes)

    if not valid_peers_all:
        print("!!! No valid peering data found for this ASN at your IXP locations !!!")
        sys.exit(1)

    deploy_list = []
    for ix_id, data in networks.items():
        ix_peers = [p for p in valid_peers_all if str(p["ix_id"]) == ix_id]
        for device in data["devices"]:
            if ix_peers:
                # Pass the prefix limits to the template
                conf = render_template(ix_peers, device, net_name, max_v4, max_v6)
                if conf:
                    deploy_list.append((device, conf, ix_peers))
                    print(f"\n--- Target: {device['hostname']} ---\n{conf}")

    if args.noop or (not args.force and input("\nDeploy to routers? [y/N]: ").lower() != 'y'):
        sys.exit(0)

    u = args.username or getpass.getuser()
    p = os.environ.get("SSH_PASSWORD") or getpass.getpass("Enter SSH password: ")

    for device, conf, ix_peers in deploy_list:
        exec_ssh(device, u, conf, p, args.ASN, ix_peers)
