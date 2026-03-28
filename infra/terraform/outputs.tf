# outputs.tf — Values displayed after terraform apply.
#
# Outputs that are printed at the end of `terraform apply` and that can be
# queried later with `terraform output`.

output "availability_domains" {
  description = "List of availability domains in the region (proves OCI connection works)"
  value       = [for ad in data.oci_identity_availability_domains.ads.availability_domains : ad.name]
}

output "region" {
  description = "OCI region we're connected to"
  value       = var.oci_region
}
