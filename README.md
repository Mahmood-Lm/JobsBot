# JobsBot (Infrastructure and DevOps)

JobsBot is a Telegram bot for job updates, deployed on AWS with an EC2 Auto Scaling Group behind an ALB and CloudFront, plus Lambda-based scraping/dispatch, DynamoDB storage, and a K3s GitOps observability stack (ArgoCD, Prometheus/Grafana, Elastic).

![Architecture diagram](assets\images\architecture1.png)

## High-Level Architecture

- AWS hosts the bot runtime (EC2 + ALB + CloudFront), the scraper and dispatcher (Lambda), and data/messaging (DynamoDB + SQS).
- A dedicated K3s observability server runs GitOps-managed logging and monitoring (ArgoCD + Prometheus/Grafana + Elastic stack).
- CI/CD is implemented with GitHub Actions to build and deploy containers and Lambda packages.

## Components and Responsibilities

### Telegram Bot Runtime (EC2 + ASG)

- Auto Scaling Group: `job-bot-asg` (min 1, max 3) behind an Application Load Balancer.
- Launch template installs Docker, runs the bot container, and starts Node Exporter.
- CloudFront provides HTTPS and acts as a webhook proxy for Telegram.
- Image source: ECR repo `linkedin-bot-v2`.

### Scraper (Lambda, Container Image)

- Function: `linkedin-scraper-function-v2`.
- Triggered by SQS (`linkedin-scraper-queue`, batch size 1).
- Image source: ECR repo `linkedin-scraper-v2`.

### Dispatcher (Lambda, Zip Package)

- Function: `linkedin-dispatcher-function`.
- Scheduled via EventBridge (rate: 1 minute).
- Pushes jobs to the scraper SQS queue.

### Data and Messaging

- DynamoDB tables: `Subscriptions-V2`, `SeenJobs-V2`, `Users-V2`.
- SQS queue: `linkedin-scraper-queue` with a dead-letter queue `linkedin-scraper-dlq`.

### GitOps and Observability (K3s)

- A dedicated EC2 instance runs K3s and serves as the observability cluster.
- ArgoCD is installed via Helm and bootstraps all observability apps using the repo as the values source.
- Helm-managed apps:
  - Prometheus + Grafana (kube-prometheus-stack)
  - Elasticsearch, Kibana, Logstash, Filebeat

## Logging and Metrics Flow

**Logging (structured JSON):**

```
Lambda/Docker stdout (JSON)
  -> Filebeat
  -> Logstash (NodePort 30092)
  -> Elasticsearch (bot-logs-YYYY.MM.dd)
  -> Kibana (NodePort 30056)
```

**Metrics:**

- Prometheus scrapes:
  - Bot business metrics on port 80
  - Node Exporter on port 9100
- Grafana is exposed via NodePort 30000.

See LOGGING.md for the JSON log schema and example queries.

## CI/CD Pipelines (GitHub Actions)

- **Bot:** Builds and pushes the bot image to ECR, then triggers an ASG instance refresh.
- **Scraper:** Builds and pushes the Lambda image, then updates the function code.
- **Dispatcher:** Zips Python sources and dependencies, then updates the Lambda function.

All workflows are triggered on `main` with path filters for their respective components.

## Provisioning (Terraform)

Terraform is the source of truth for AWS resources and the observability stack. The Terraform configuration expects a local kubeconfig at `terraform/kubeconfig.yaml` for Helm deployments.

1. Set required variables in `terraform/terraform.tfvars`:
   - `telegram_token`
   - `gemini_api_key`
   - `elastic_password`
2. Run Terraform in `terraform/`:
   - `terraform init`
   - `terraform apply`
3. Retrieve the K3s kubeconfig from the observability server and save it to `terraform/kubeconfig.yaml`.
4. Re-run `terraform apply` to install ArgoCD and the GitOps-managed charts.

## Access Points (Public NodePorts)

- **ArgoCD:** NodePort 30443
- **Grafana:** NodePort 30000
- **Prometheus:** NodePort 30090
- **Kibana:** NodePort 30056
- **Logstash (beats input):** NodePort 30092
- **Kubernetes API:** 6443

## Dashboards (Placeholders)

**ArgoCD**

![ArgoCD dashboard](assets\images\argocd1.png)

**Grafana**

![Grafana dashboard](assets\images\grafana1.png)

**Kibana**

![Kibana dashboard](assets\images\kibana1.png)

## Telegram Bot

- Bot link: [Open in Telegram](<PLACEHOLDER_TELEGRAM_BOT_URL>)

## Environment Scope

- Single environment (current deployment).

## DevOps-Focused Repo Map

- `terraform/` : AWS infrastructure and K3s/GitOps provisioning
- `cluster-config/` : Helm values for logging and monitoring stacks
- `.github/workflows/` : CI/CD pipelines
- `bot/` : EC2 container build for the Telegram bot
- `scraper/` : Lambda container image for the scraper
- `dispatcher/` : Lambda zip package for the dispatcher
- `shared/` : Shared code, including structured logging helpers

## Configuration and Secrets

- Terraform inputs: `telegram_token`, `gemini_api_key`, `elastic_password`
- GitHub Actions secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`