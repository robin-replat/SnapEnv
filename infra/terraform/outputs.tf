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

output "vcn_id" {
  description = "ID of the VCN created"
  value       = oci_core_virtual_network.snapenv_vcn.id
}

output "subnet_id" {
  description = "ID of the public subnet"
  value       = oci_core_subnet.snapenv_public.id
}

output "my_public_ip" {
  description = "My current public IP (used for SSH and K8s API access)"
  value       = trimspace(data.http.my_ip.response_body)
}
