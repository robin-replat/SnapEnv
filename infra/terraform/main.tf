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
