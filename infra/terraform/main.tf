# main.tf
#
# This file:
# 1. Declares which Terraform version and providers we need
# 2. Configures the OCI provider with our credentials
# 3. Reads the availability domains (proves the connection works)

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    # The OCI provider is a Terraform plugin that knows how to
    # call Oracle Cloud APIs to create/read/update/delete resources.
    oci = {
      source  = "oracle/oci"
      version = "~> 6.0"
    }
  }
}

# Configure the OCI provider with our API credentials.
# These values come from variables.tf → terraform.tfvars.
provider "oci" {
  tenancy_ocid     = var.oci_tenancy_ocid
  user_ocid        = var.oci_user_ocid
  fingerprint      = var.oci_fingerprint
  private_key_path = var.oci_private_key_path
  region           = var.oci_region
}

# ── Data Source ───────────────────────────────────
# Reads information from the cloud without creating anything.
# This fetches the list of availability domains in our region.
# Ensure that credentials are correctly configured.

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.oci_tenancy_ocid
}


# ── Network ───────────────────────────────────────

# Virtual Cloud Network — the isolated private network.
# All resources (servers, databases) live inside this VCN.
resource "oci_core_virtual_network" "snapenv_vcn" {
  compartment_id = var.oci_compartment_ocid
  cidr_block     = "10.0.0.0/16"
  display_name   = "snapenv-vcn"
  dns_label      = "snapenv"
}

# Internet Gateway — connects the VCN to the public internet.
# Allow VCN to be reached by the public world

resource "oci_core_internet_gateway" "snapenv_igw" {
  compartment_id = var.oci_compartment_ocid
  vcn_id         = oci_core_virtual_network.snapenv_vcn.id
  display_name   = "snapenv-igw"
}

# Route Table — routing rules for the subnet.
# This single rule says: "for any destination not in the VCN,
# send the traffic through the internet gateway."
resource "oci_core_route_table" "snapenv_rt" {
  compartment_id = var.oci_compartment_ocid
  vcn_id         = oci_core_virtual_network.snapenv_vcn.id
  display_name   = "snapenv-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.snapenv_igw.id
  }
}

# Public Subnet — the network segment where the server is placed.
# It uses 10.0.1.0/24 (256 addresses) out of the VCN's 10.0.0.0/16.
# Attached to the route table so traffic can reach the internet.
resource "oci_core_subnet" "snapenv_public" {
  compartment_id = var.oci_compartment_ocid
  vcn_id         = oci_core_virtual_network.snapenv_vcn.id
  cidr_block     = "10.0.1.0/24"
  display_name   = "snapenv-public-subnet"
  dns_label      = "public"
  route_table_id = oci_core_route_table.snapenv_rt.id
}
