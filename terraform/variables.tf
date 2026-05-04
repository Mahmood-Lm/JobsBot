variable "aws_region" {
  default = "eu-central-1"
}
variable "telegram_token" {
  type      = string
  sensitive = true
}
variable "gemini_api_key" {
  description = "API Key for Google Gemini"
  type        = string
  sensitive   = true
}
variable "elastic_password" {
  description = "Static password for Elasticsearch to prevent GitOps drift"
  type        = string
  sensitive   = true
}