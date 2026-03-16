# Peering Provisioning Script

A small automation tool to help provision BGP peering sessions using data from **PeeringDB**.

The script takes a peer ASN, looks it up in PeeringDB, checks which IXPs the network is present at, and generates router configuration using **Jinja2 templates**. If desired, it can also log into the router and apply the configuration.

The main goal is to remove the repetitive work involved in setting up IXP peers while still keeping the process simple and transparent.

---

## What it does

Given an ASN, the script will:

1. Query PeeringDB for the network
2. Determine which IXPs the network participates in
3. Match those IXPs with routers defined in `routers.cfg`
4. Generate configuration from templates
5. Display the config for review
6. Optionally push the config to the router over SSH

The script is designed so that the generated configuration is always visible before anything is deployed.


## Supported platforms

The script currently includes template support for:

* Cisco IOS / IOS-XE
* Cisco IOS-XR
* Arista EOS
* Juniper JunOS

Additional platforms can be added easily by creating a new template and adding the platform to the `BACKUP_COMMANDS` and `VERIFY_COMMANDS` dictionaries.



## Installation

Clone the repository:

```
git clone https://github.com/andrew-claassen/peering_config.git
cd peering_prov
```

Install the Python dependencies:

```
pip install -r requirements.txt
```

The script requires Python 3.8 or newer.

1. First edit get_ixp_info.py and set your network ID. If you are unsure howto find this, goto www.peeringdb.com and search for your ASN.
Click on your network it will open the network page, the network ID is the last number in the URL.
eg. ZANOG ASN is 37262, the URL is https://www.peeringdb.com/net/17650 so the network ID is 17650

NB! don't use this network ID you MUST find your own network ID!

2. Ok now run python3 get_ixp_info.py

This will create a file called ixp.cfg with the following example information:

Name: SOME_NETWORK_NAME
Prefixes v4: 1
Prefixes v6: 1
IX ID: 1, Name: SOMEIXP, IPv4: 192.0.1.111, IPv6: 2001:xxxx:aaaa:1:111

This is the information we need to configure our routers for peering.

3. Edit peering.py and set your network ID in the OUR_NETWORK_IDS = ['1234'] variable.

4. Edit routers.cfg and add your router's hostname/ip addresses and the device type, also name your exchange points to what you prefer, this is the text that will be used to render the templates.

5. Now edit the templates in the templates folder to suit your needs, the ones here are just examples. These are Jinja2 templates and you can use any of the variables that are passed to the template.

6. Now run python3 peering.py ASN
eg. python3 peering.py 1234

```
usage: peering.py [-h] [-v] [-f] [-n] [-u USERNAME] ASN

positional arguments:
  ASN

options:
  -h, --help            show this help message and exit
  -v, --verbose
  -f, --force
  -n, --noop
  -u USERNAME, --username USERNAME
```

## Supported platforms

The script currently includes template support for:

* Cisco IOS / IOS-XE
* Cisco IOS-XR
* Arista EOS
* Juniper JunOS

Additional platforms can be added easily by creating a new template and adding the platform to the `BACKUP_COMMANDS` and `VERIFY_COMMANDS` dictionaries.

