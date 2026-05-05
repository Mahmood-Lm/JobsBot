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

data "aws_caller_identity" "current" {}

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
    # --- PROMETHEUS STACK ---
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
              parameters:
                - name: "secret.password"
                  value: "${var.elastic_password}"
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

      # --- KIBANA (UI) ---
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
            
      # --- LOGSTASH ---
      logstash:               
        namespace: argocd
        project: default
        sources:
          - repoURL: 'https://helm.elastic.co'
            chart: logstash
            targetRevision: 8.5.1
            helm:
              valueFiles:
                - $values/cluster-config/logging/logstash-values.yaml
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

      # --- FILEBEAT ---
      filebeat:               
        namespace: argocd
        project: default
        sources:
          - repoURL: 'https://helm.elastic.co'
            chart: filebeat
            targetRevision: 8.5.1
            helm:
              valueFiles:
                - $values/cluster-config/logging/filebeat-values.yaml
              parameters:
                # Override the entire filebeat.yml content at deploy time
                - name: "filebeatConfig.filebeat\\.yml"
                  value: |
                    filebeat.inputs:
                      - type: aws-cloudwatch
                        log_group_arn: "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/linkedin-scraper-function-v2:*"
                        region: "${var.aws_region}"
                        scan_frequency: 1m 
                        api_timeout: 120s       
                    output.logstash:
                      hosts: ["logstash-logstash:30092"]
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