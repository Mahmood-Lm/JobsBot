# --- 1. THE HELM PROVIDER ---
provider "helm" {
  kubernetes = {
    config_path = "${path.module}/kubeconfig.yaml"
    insecure    = true #TODO: For production, set up proper certs and remove this line #Ignore the Public IP cert mismatch
  }
}

# --- 2. THE ARGOCD INSTALLATION ---
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  namespace        = "argocd"
  create_namespace = true
  version          = "5.51.6"

  # The v3 Provider Syntax: A List of Objects
  set = [{
    name  = "server.service.type"
    value = "NodePort"
  }]
}