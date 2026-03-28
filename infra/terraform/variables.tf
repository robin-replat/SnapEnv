# variables.tf — Input variables for OCI credentials.
#
# Variables are declared here (name, type, description).
# Values are assigned in terraform.tfvars (gitignored).

variable "oci_tenancy_ocid" {
  description = "OCID of the OCI tenancy (found in Profile → Tenancy)"
  type        = string
}

variable "oci_user_ocid" {
  description = "OCID of the OCI user (found in Profile → My Profile)"
  type        = string
}

variable "oci_fingerprint" {
  description = "Fingerprint of the OCI API signing key (shown when you upload the public key)"
  type        = string
}

variable "oci_private_key_path" {
  description = "Path to the OCI API private key file on your machine"
  type        = string
}

variable "oci_region" {
  description = "OCI region identifier (e.g., eu-amsterdam-1)"
  type        = string
  default     = "eu-paris-1"
}

variable "oci_compartment_ocid" {
  description = "OCID of the compartment to create resources in (use tenancy OCID for root)"
  type        = string
}
