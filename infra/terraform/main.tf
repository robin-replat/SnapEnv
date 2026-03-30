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

    http = {
      source  = "hashicorp/http"
      version = "~> 3.0"
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

# ── Get admin IP ───────────────────────────────────
# Fetch my public IP address automatically.
# This calls an external API at plan/apply time so the security rules
# always match my current IP — no manual updates needed.
# If my IP changes, run `terraform apply` again.
data "http" "my_ip" {
  url = "https://ifconfig.me/ip"
}

locals {
  # Trim whitespace and add /32 mask
  my_ip_cidr = "${trimspace(data.http.my_ip.response_body)}/32"
}


# ── Data Source ───────────────────────────────────
# Reads information from the cloud without creating anything.
# This fetches the list of availability domains in our region.
# Ensure that credentials are correctly configured.

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.oci_tenancy_ocid
}

# Find the latest Oracle Linux 9 ARM image.
# This data source automatically picks the most recent one.
data "oci_core_images" "oracle_linux" {
  compartment_id           = var.oci_compartment_ocid
  operating_system         = "Oracle Linux"
  operating_system_version = "9"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
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
  security_list_ids = [oci_core_security_list.snapenv_sl.id]
}

# Security List — network-level firewall rules.
# Controls which ports are open for incoming (ingress) and outgoing (egress) traffic.
# Without these rules, even SSH would be blocked.
resource "oci_core_security_list" "snapenv_sl" {
  compartment_id = var.oci_compartment_ocid
  vcn_id         = oci_core_virtual_network.snapenv_vcn.id
  display_name   = "snapenv-security-list"

  # ── Outbound: allow everything ──────────────────
  # The server can reach any external service (apt repos, Docker Hub, GitHub, etc.)
  # Egress security can be improved but as the current state of the project is a lab dev
  # I allow everything for convenience. Egress risks come after being compromise (data exfiltration, etc.)
  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  # ── Inbound: only specific ports ───────────────

  # SSH (port 22) — remote administration
  ingress_security_rules {
    protocol = "6" # 6 = TCP (defined by IANA protocol numbers)
    source   = local.my_ip_cidr
    tcp_options {
      min = 22
      max = 22
    }
  }

  # HTTP (port 80) — Nginx Ingress receives traffic here
  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 80
      max = 80
    }
  }

  # HTTPS (port 443) — TLS-encrypted traffic
  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 443
      max = 443
    }
  }

  # K8s API (port 6443) — kubectl access from your machine
  # In a real production setup you'd restrict this to your IP only.
  # For this project, open to all is acceptable.
  ingress_security_rules {
    protocol = "6"
    source   = local.my_ip_cidr
    tcp_options {
      min = 6443
      max = 6443
    }
  }
}

# ── Compute Instance ──────────────────────────────

# This creates the actual server — an ARM VM on Oracle Cloud's free tier.
# The server gets a public IP automatically and SSH key
# is injected to log in immediately after creation.
resource "oci_core_instance" "snapenv_server" {
  compartment_id      = var.oci_compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "snapenv-server"

  # ARM Ampere A1 Flex — free tier shape
  shape = "VM.Standard.A1.Flex"

  # How much compute to allocate (within free tier limits)
  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gb
  }

  # Network configuration — public subnet place with a public IP
  create_vnic_details {
    subnet_id        = oci_core_subnet.snapenv_public.id
    display_name     = "snapenv-nic"
    assign_public_ip = true
    hostname_label   = "snapenv"
  }

  # OS image — use the latest Oracle Linux 9 ARM image
  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.oracle_linux.images[0].id
    # 50 GB boot volume (free tier allows up to 200 GB total)
    boot_volume_size_in_gbs = 50
  }

  # Inject the SSH public key to be able to connect immediately
  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
  }

  timeouts {
    create = "15m"
  }
}
