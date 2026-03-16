#!/usr/bin/env python3
"""
PeeringDB Network Exporter
--------------------------
This script fetches network configuration and IXP peering details from the 
PeeringDB API and exports them to a local configuration file (ixp.cfg).
"""

import requests

# The PeeringDB unique identifier for your network. 
# You can find this in the URL of your network's PeeringDB page.
OUR_NETWORK_ID = '1234'

def main() -> None:
    url = f"https://www.peeringdb.com/api/net/{OUR_NETWORK_ID}"

    try:
        # Request data from PeeringDB API
        response = requests.get(url, timeout=20)
        response.raise_for_status() 
        
        # Parse JSON response; PeeringDB returns a list under the "data" key
        data = response.json()
        if not data.get("data"):
            print(f"Error: No data found for Network ID {OUR_NETWORK_ID}")
            return

        network_info = data["data"][0]

        # Generate the configuration file
        with open("ixp.cfg", "w", encoding="utf-8") as cfg:
            # Write high-level network metadata
            cfg.write(f"Name: {network_info['name']}\n")
            cfg.write(f"Prefixes v4: {network_info['info_prefixes4']}\n")
            cfg.write(f"Prefixes v6: {network_info['info_prefixes6']}\n")
            cfg.write("-" * 40 + "\n") # Visual separator for readability

            # Iterate through the set of IXPs where this network is present
            for ix in network_info.get("netixlan_set", []):
                line = (
                    f"IX ID: {ix['ix_id']}, "
                    f"Name: {ix['name']}, "
                    f"IPv4: {ix['ipaddr4']}, "
                    f"IPv6: {ix['ipaddr6']}\n"
                )
                cfg.write(line)

        print("Success: Output written to ixp.cfg")

    except requests.exceptions.RequestException as e:
        print(f"Network error occurred: {e}")

if __name__ == "__main__":
    main()
