#!/usr/bin/env python3
"""
Copyright (c) 2023 Cisco and/or its affiliates.
This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at
https://developer.cisco.com/docs/licenses
All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
"""

__author__ = "Trevor Maco <tmaco@cisco.com>"
__copyright__ = "Copyright (c) 2023 Cisco and/or its affiliates."
__license__ = "Cisco Sample Code License, Version 1.1"

import os
import sys

import meraki
from meraki import APIError
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.prompt import Confirm, IntPrompt
from dotenv import load_dotenv

import config

# Load ENV Variable
load_dotenv()
API_KEY = os.getenv("API_KEY")


# Rich Console Instance
console = Console()

# Create a Meraki API client
dashboard = meraki.DashboardAPI(API_KEY, suppress_logging=True)


def get_org_id(org_name):
    """
    Get Org Id from Org Name
    :param org_name: Org name
    :return: Org ID
    """
    orgs = dashboard.organizations.getOrganizations()
    org_id = None
    for org in orgs:
        if org['name'] == org_name:
            org_id = org['id']

    if not org_id:
        console.print(f"[red]Error: Org. Name {config.ORG_NAME} not found...[/]")
        sys.exit(-1)
    else:
        return org_id


def match_dhcp_dns_values(current_dns_values_list, match_behavior):
    """
    Match VLAN DNS Custom Nameservers based on desired match behavior. Supported behaviors include, matching: all
    VLANs (1), VLANs with the exact old dns values configured (2), and VLANs which contain all old values (3)
    :param current_dns_values_list: Current list of configured DNS Nameservers on the VLAN
    :param match_behavior: Desired Match Behavior
    :return: True/False for a match
    """
    # Perform match of Old DHCP DNS Nameserver values depending on match criteria
    if match_behavior == 1:
        # All Case: always return true
        return True
    elif match_behavior == 2:
        # Exact match case: confirm old list matches vlan's current list (order irrelevant) - length check to prevent
        # contains mismatch
        return len(current_dns_values_list) == len(config.OLD_DHCP_DNS_VALUES) and all(
            old_dns_value in current_dns_values_list for old_dns_value in config.OLD_DHCP_DNS_VALUES)
    elif match_behavior == 3:
        # Find and Replace case: see if old list is contained in current list (order irrelevant)
        return all(old_dns_value in current_dns_values_list for old_dns_value in config.OLD_DHCP_DNS_VALUES)
    else:
        return False


def create_new_dns_values_list(current_dns_values_list, match_behavior, overwrite):
    """
    Build new VLAN DNS Custom Nameserver list based on desired match behavior. Supported behaviors include,
    return: only the new list or a combined list with previous values depending on overwrite (1), only the new list (
    2), a list with old values removed and new values appended - untouched values retained (3)
    :param current_dns_values_list: Current list of configured DNS Nameservers on the VLAN
    :param match_behavior: Desired Match Behavior
    :param overwrite: Controls replacing existing Nameservers when 'all' matching behavior selected
    :return: A new list of DNS Nameservers
    """
    # Create new dhcp values list depending on original match criteria and overwrite
    if match_behavior == 1:
        # All Case: return new list if overwrite configure, otherwise combined list
        if overwrite:
            return config.NEW_DHCP_DNS_VALUES
        else:
            return list(set(config.NEW_DHCP_DNS_VALUES + current_dns_values_list))
    elif match_behavior == 2:
        # Exact match case: always overwrite to remove old values
        return config.NEW_DHCP_DNS_VALUES
    else:
        # Find and Replace case: replace old values in current list with new values, but retain untouched values
        # Remove old values
        new_values = list(set(current_dns_values_list) - set(config.OLD_DHCP_DNS_VALUES))

        # Add new values that aren't already present
        new_values.extend(value for value in config.NEW_DHCP_DNS_VALUES if value not in new_values)

        return new_values


def update_dhcp_dns_ips(networks, match_behavior, overwrite):
    """
    Update DHCP DNS Nameservers for all MX VLANs based on matching criteria
    :param networks: List of all 'Appliance' (MX) networks
    :param match_behavior: Desired match behavior
    :param overwrite: Controls replacing existing DNS Nameservers
    """
    with Progress() as progress:
        overall_progress = progress.add_task("Overall Progress", total=len(networks))
        counter = 1

        for network in networks:
            progress.console.print(
                "Processing Network: [blue]{}[/] ({} of {})".format(network['name'], str(counter), len(networks)))

            # Get Existing VLANs in Network
            network_vlans = dashboard.appliance.getNetworkApplianceVlans(network['id'])

            # DNS Options to ignore
            ignore_options = ['upstream_dns', 'google_dns', 'opendns']

            # Structures for console output
            vlan_task = progress.add_task("Processing VLANs...", total=len(network_vlans))
            updated_vlans = {}

            # Iterate and process each vlan (only match VLANs running DHCP and custom DNS nameservers)
            for vlan in network_vlans:
                if vlan['dhcpHandling'] == 'Run a DHCP server' and vlan['dnsNameservers'] not in ignore_options:
                    current_dns_values_list = vlan['dnsNameservers'].split('\n')

                    # Perform match based on selected behavior, determine if we need to update this vlan
                    match = match_dhcp_dns_values(current_dns_values_list, match_behavior)

                    # We found a match!
                    if match:
                        # Get list of new values (depending on match criteria and overwrite)
                        newDnsNameservers = create_new_dns_values_list(current_dns_values_list, match_behavior,
                                                                       overwrite)
                        newDnsNameservers.sort()

                        # Convert list to meraki api compatible format (\n separated string)
                        if len(newDnsNameservers) > 1:
                            newDnsNameservers_string = "\n".join(newDnsNameservers)
                        else:
                            newDnsNameservers_string = newDnsNameservers[0]

                        try:
                            # Update DNS Nameservers for VLAN DHCP
                            response = dashboard.appliance.updateNetworkApplianceVlan(network['id'], vlan['id'],
                                                                                      dnsNameservers=newDnsNameservers_string)

                            # Update for display
                            dict_string = f"{vlan['name']} ({vlan['id']})"
                            updated_vlans[dict_string] = newDnsNameservers
                        except APIError as e:
                            progress.console.print(f'-- [red]Error: {str(e)}[/]')

                progress.update(vlan_task, advance=1)

            # Cleanup Intermediate VLAN progress display (ignore first task -> overall task), display changed VLANs
            progress.remove_task(progress.task_ids[1])
            if len(updated_vlans) > 0:
                progress.console.print(f'[yellow]Updated VLANs (DHCP DNS Nameserver(s)):\n{updated_vlans}[/]')
            else:
                progress.console.print('[green]No VLANs were updated![/]')

            counter += 1
            progress.update(overall_progress, advance=1)


def main():
    console.print(Panel.fit("Meraki VLANs - DHCP DNS Nameserver(s) Bulk Update Tool"))

    # Find org id
    console.print(Panel.fit("Get Org ID", title="Step 1"))
    org_id = get_org_id(config.ORG_NAME)

    console.print(f"Found {org_id} for [green]{config.ORG_NAME}![/]")

    # Get appliance networks in org
    console.print(Panel.fit("Get Appliance Networks", title="Step 2"))
    networks = dashboard.organizations.getOrganizationNetworks(organizationId=org_id, total_pages='all')
    networks = [network for network in networks if 'appliance' in network['productTypes']]

    console.print(f"Found {len(networks)} Appliance Networks")

    # Replace DHCP DNS Old Values with New Values
    console.print(Panel.fit("Update Old DNS Nameserver(s) to New Nameserver(s)", title="Step 3"))

    # Prompts, Controls matching behavior
    match_behavior = IntPrompt.ask(
        "Select an Old DNS Nameserver match criteria. Options are explained in the README.\n1.) All\n2.) Exact "
        "Match\n3.) Find and Replace\nSelection", choices=["1", "2", "3"],
        show_choices=False, show_default=False)

    # If 'all' option selected, determine if overwrite (replace all), or append
    overwrite = None
    if match_behavior == 1:
        overwrite = Confirm.ask("Would you like to overwrite existing DNS Nameserver(s) on match?")

    print()
    update_dhcp_dns_ips(networks, match_behavior, overwrite)


if __name__ == "__main__":
    main()
