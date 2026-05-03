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

  # The v3 Provider Syntax: A List of Objects
  set = [{
    name  = "server.service.type"
    value = "NodePort"
  }]
}

# --- THE GITOPS BOOTSTRAPPER ---
# This automatically injects the first GitHub repository into ArgoCD
resource "helm_release" "argocd_apps" {
  name       = "argocd-apps"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  namespace  = "argocd"
  
  # CRITICAL: Do not run this until ArgoCD is fully installed!
  depends_on = [helm_release.argocd]

values = [
    <<-EOF
    applications:
      prometheus-stack:               
        namespace: argocd
        project: default
        sources:
          # Source 1: The Helm Chart
          - repoURL: 'https://prometheus-community.github.io/helm-charts'
            chart: kube-prometheus-stack
            targetRevision: 60.0.2 
            helm:
              # This tells Argo: "Go find the 'values' source and look inside it"
              valueFiles:
                - $values/cluster-config/monitoring/prometheus-custom-values.yaml
          
          # Source 2: Your GitHub Repo (The 'values' source)
          - repoURL: 'https://github.com/Mahmood-Lm/JobsBot.git'
            targetRevision: HEAD
            ref: values         # This creates the $values alias used above
            
        destination:
          server: 'https://kubernetes.default.svc'
          namespace: monitoring
        syncPolicy:
          automated:
            prune: true
            selfHeal: true 
          syncOptions:
            - CreateNamespace=true
            - ServerSideApply=true

      # --- ELASTICSEARCH ---
      elasticsearch:               
        namespace: argocd
        project: default
        sources:
          - repoURL: 'https://helm.elastic.co'
            chart: elasticsearch
            targetRevision: 8.5.1
            helm:
              valueFiles:
                - $values/cluster-config/logging/elastic-values.yaml
          - repoURL: 'https://github.com/Mahmood-Lm/JobsBot.git'
            targetRevision: HEAD
            ref: values 
        destination:
          server: 'https://kubernetes.default.svc'
          namespace: logging
        syncPolicy:
          automated:
            prune: true
            selfHeal: true 
          syncOptions:
            - CreateNamespace=true
            - ServerSideApply=true

      # --- KIBANA ---
      kibana:               
        namespace: argocd
        project: default
        sources:
          - repoURL: 'https://helm.elastic.co'
            chart: kibana
            targetRevision: 8.5.1
            helm:
              valueFiles:
                - $values/cluster-config/logging/kibana-values.yaml
          - repoURL: 'https://github.com/Mahmood-Lm/JobsBot.git'
            targetRevision: HEAD
            ref: values 
        destination:
          server: 'https://kubernetes.default.svc'
          namespace: logging
        syncPolicy:
          automated:
            prune: true
            selfHeal: true
    EOF
  ]
}