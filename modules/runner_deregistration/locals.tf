locals {
  norm_arch = contains(["arm64", "aarch64"], var.architecture) ? "aarch64" : (
    contains(["x86_64", "amd64"], var.architecture) ? "x86_64" : var.architecture
  )
}
